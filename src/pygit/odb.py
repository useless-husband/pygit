"""The object database: loose objects plus packfiles."""
from __future__ import annotations

import glob
import os
import re
import tempfile
import zlib

from .objects import decode_object, encode_object, hash_bytes, OBJECT_TYPES
from .pack import PackFile
from .util import GitError

_SHA = re.compile(r"^[0-9a-f]{40}$")


class ObjectDB:
    def __init__(self, path: str):
        self.path = path
        self._packs: list[PackFile] | None = None

    # ---- packs
    def _load_packs(self, refresh: bool = False) -> list[PackFile]:
        if self._packs is None or refresh:
            known = {p.idx_path: p for p in (self._packs or [])}
            packs = []
            for idx in sorted(glob.glob(os.path.join(self.path, "pack", "pack-*.idx"))):
                if idx in known:
                    packs.append(known[idx])
                elif os.path.exists(idx[:-4] + ".pack"):
                    packs.append(PackFile(idx, resolver=self.read_hex))
            self._packs = packs
        return self._packs

    def close(self) -> None:
        for pack in self._packs or []:
            pack.close()
        self._packs = None

    def read_hex(self, sha: str):
        return self.read(sha)

    # ---- reading
    def _loose_path(self, sha: str) -> str:
        return os.path.join(self.path, sha[:2], sha[2:])

    def read(self, sha: str) -> tuple[str, bytes]:
        if not _SHA.match(sha):
            raise GitError(f"Not a valid object name: '{sha}'")
        try:
            with open(self._loose_path(sha), "rb") as f:
                compressed = f.read()
        except FileNotFoundError:
            pass
        else:
            try:
                return decode_object(zlib.decompress(compressed))
            except zlib.error:
                raise GitError(f"object {sha} is corrupt (bad zlib data)") from None
        for refresh in (False, True):
            for pack in self._load_packs(refresh):
                found = pack.read(sha)
                if found is not None:
                    return found
        raise GitError(f"Not a valid object name: '{sha}'")

    def exists(self, sha: str) -> bool:
        if not _SHA.match(sha):
            return False
        if os.path.exists(self._loose_path(sha)):
            return True
        for refresh in (False, True):
            for pack in self._load_packs(refresh):
                if pack.find(bytes.fromhex(sha)) is not None:
                    return True
        return False

    def find_prefix(self, prefix: str) -> list[str]:
        """All object ids starting with the (hex) prefix."""
        found = set()
        d = os.path.join(self.path, prefix[:2])
        if os.path.isdir(d):
            for name in os.listdir(d):
                if len(name) == 38 and (prefix[:2] + name).startswith(prefix):
                    found.add(prefix[:2] + name)
        for pack in self._load_packs(True):
            found.update(pack.prefix_matches(prefix))
        return sorted(found)

    # ---- writing
    def write(self, obj_type: str, data: bytes) -> str:
        if obj_type not in OBJECT_TYPES:
            raise GitError(f"invalid object type '{obj_type}'")
        sha = hash_bytes(obj_type, data)
        if self.exists(sha):
            return sha
        path = self._loose_path(sha)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="tmp_obj_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(zlib.compress(encode_object(obj_type, data)))
            os.chmod(tmp, 0o444)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        return sha
