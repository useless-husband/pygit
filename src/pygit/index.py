"""Reading and writing the ``.git/index`` file (DIRC, version 2)."""
from __future__ import annotations

import hashlib
import os
import struct

from .util import GitError, pkey

SIGNATURE = b"DIRC"
_FIXED = struct.Struct(">10I20sH")   # 62 bytes: ctime, mtime, dev, ino, mode, uid, gid, size, sha, flags
assert _FIXED.size == 62


class IndexEntry:
    __slots__ = ("ctime_s", "ctime_ns", "mtime_s", "mtime_ns", "dev", "ino", "mode",
                 "uid", "gid", "size", "sha", "flags", "path")

    def __init__(self, path, sha, mode, size=0, ctime_s=0, ctime_ns=0, mtime_s=0, mtime_ns=0,
                 dev=0, ino=0, uid=0, gid=0, flags=None):
        self.path = path
        self.sha = sha
        self.mode = mode
        self.size = size
        self.ctime_s, self.ctime_ns = ctime_s, ctime_ns
        self.mtime_s, self.mtime_ns = mtime_s, mtime_ns
        self.dev, self.ino, self.uid, self.gid = dev, ino, uid, gid
        self.flags = min(len(pkey(path)), 0xFFF) if flags is None else flags

    @property
    def stage(self) -> int:
        return (self.flags >> 12) & 3

    def __repr__(self) -> str:
        return f"IndexEntry({self.path!r}, {self.sha[:7]}, {self.mode:o})"


class Index:
    """In-memory index: ``entries`` maps path -> IndexEntry (stage 0 only)."""

    def __init__(self, entries=None, mtime_ns: int = 0):
        self.entries: dict[str, IndexEntry] = dict(entries or {})
        self.mtime_ns = mtime_ns      # mtime of the index file, used for "racy" checks

    def sorted_entries(self) -> list[IndexEntry]:
        return sorted(self.entries.values(), key=lambda e: pkey(e.path))


def parse_index(data: bytes) -> Index:
    if len(data) < 32 or data[:4] != SIGNATURE:
        raise GitError("index file is corrupt: bad signature")
    version, count = struct.unpack(">II", data[4:12])
    if version not in (2, 3):
        raise GitError(f"unsupported index version {version} (pygit reads versions 2 and 3; "
                       "run 'git update-index --index-version 2' to convert)")
    if hashlib.sha1(data[:-20]).digest() != data[-20:]:
        raise GitError("index file is corrupt: checksum mismatch")
    end = len(data) - 20
    pos = 12
    entries: dict[str, IndexEntry] = {}
    for _ in range(count):
        start = pos
        if pos + 62 > end:
            raise GitError("index file is corrupt: truncated entry")
        (cs, cn, ms, mn, dev, ino, mode, uid, gid, size, sha, flags) = _FIXED.unpack_from(data, pos)
        pos += 62
        if version == 3 and flags & 0x4000:      # extended flags (skip-worktree, intent-to-add)
            pos += 2
        name_len = flags & 0xFFF
        if name_len < 0xFFF:
            name = data[pos:pos + name_len]
        else:
            name = data[pos:data.index(b"\0", pos)]
        pos = start + ((pos - start + len(name) + 8) & ~7)
        path = name.decode("utf-8", "surrogateescape")
        entry = IndexEntry(path, sha.hex(), mode, size, cs, cn, ms, mn, dev, ino, uid, gid, flags)
        if entry.stage:
            raise GitError("index contains unmerged entries; pygit does not support merge conflicts")
        entries[path] = entry
    # Extensions (TREE, REUC, UNTR, ...): 4-byte signature + 4-byte size. Optional ones
    # (uppercase first letter) can be ignored; a lowercase one is required for correctness.
    while pos + 8 <= end:
        sig = data[pos:pos + 4]
        (size,) = struct.unpack(">I", data[pos + 4:pos + 8])
        if not sig[:1].isupper():
            raise GitError(f"unsupported required index extension '{sig.decode('ascii', 'replace')}'")
        pos += 8 + size
    return Index(entries)


def serialize_index(entries) -> bytes:
    ordered = sorted(entries, key=lambda e: pkey(e.path))
    out = [SIGNATURE, struct.pack(">II", 2, len(ordered))]
    for e in ordered:
        name = pkey(e.path)
        flags = min(len(name), 0xFFF) | (e.stage << 12)
        head = _FIXED.pack(e.ctime_s & 0xFFFFFFFF, e.ctime_ns & 0xFFFFFFFF, e.mtime_s & 0xFFFFFFFF,
                           e.mtime_ns & 0xFFFFFFFF, e.dev & 0xFFFFFFFF, e.ino & 0xFFFFFFFF, e.mode,
                           e.uid & 0xFFFFFFFF, e.gid & 0xFFFFFFFF, e.size & 0xFFFFFFFF,
                           bytes.fromhex(e.sha), flags)
        total = 62 + len(name)
        out.append(head + name + b"\0" * (((total + 8) & ~7) - total))
    body = b"".join(out)
    return body + hashlib.sha1(body).digest()


def read_index(path: str) -> Index:
    try:
        with open(path, "rb") as f:
            data = f.read()
        mtime = os.stat(path).st_mtime_ns
    except FileNotFoundError:
        return Index()
    idx = parse_index(data)
    idx.mtime_ns = mtime
    return idx


def write_index(path: str, index: Index) -> None:
    data = serialize_index(index.entries.values())
    tmp = path + ".lock"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    index.mtime_ns = os.stat(path).st_mtime_ns
