"""Read-only support for packfiles (``.pack``) and their v2 indexes (``.idx``)."""
from __future__ import annotations

import bisect
import mmap
import os
import struct
import zlib

from .util import GitError

_TYPE_NAMES = {1: "commit", 2: "tree", 3: "blob", 4: "tag"}
OFS_DELTA = 6
REF_DELTA = 7


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    """Little-endian base-128 integer used in delta headers."""
    result = shift = 0
    while True:
        c = buf[pos]
        pos += 1
        result |= (c & 0x7F) << shift
        shift += 7
        if not c & 0x80:
            return result, pos


def apply_delta(base: bytes, delta: bytes) -> bytes:
    """Rebuild an object from ``base`` and a git delta (copy / insert instructions)."""
    src_size, pos = _read_varint(delta, 0)
    dst_size, pos = _read_varint(delta, pos)
    if src_size != len(base):
        raise GitError("corrupt delta: base size mismatch")
    out = bytearray()
    while pos < len(delta):
        op = delta[pos]
        pos += 1
        if op & 0x80:
            offset = size = 0
            for i in range(4):
                if op & (1 << i):
                    offset |= delta[pos] << (8 * i)
                    pos += 1
            for i in range(3):
                if op & (0x10 << i):
                    size |= delta[pos] << (8 * i)
                    pos += 1
            if size == 0:
                size = 0x10000
            if offset + size > len(base):
                raise GitError("corrupt delta: copy out of range")
            out += base[offset:offset + size]
        elif op:
            out += delta[pos:pos + op]
            pos += op
        else:
            raise GitError("corrupt delta: reserved opcode 0")
    if len(out) != dst_size:
        raise GitError("corrupt delta: result size mismatch")
    return bytes(out)


class PackFile:
    """One ``pack-*.pack`` plus its ``pack-*.idx``."""

    def __init__(self, idx_path: str, resolver=None):
        self.idx_path = idx_path
        self.pack_path = idx_path[:-4] + ".pack"
        self.resolver = resolver    # callable(hex_sha) -> (type, data), for external REF_DELTA bases
        with open(idx_path, "rb") as f:
            self._idx = f.read()
        idx = self._idx
        if idx[:4] != b"\xfftOc" or struct.unpack(">I", idx[4:8])[0] != 2:
            raise GitError(f"unsupported pack index (only v2 is supported): {idx_path}")
        self._fanout = struct.unpack(">256I", idx[8:8 + 1024])
        self.count = self._fanout[255]
        self._sha_start = 8 + 1024
        self._crc_start = self._sha_start + 20 * self.count
        self._off_start = self._crc_start + 4 * self.count
        self._big_start = self._off_start + 4 * self.count
        self._file = open(self.pack_path, "rb")
        self._pack = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        if self._pack[:4] != b"PACK":
            raise GitError(f"not a packfile: {self.pack_path}")
        self._cache: dict[int, tuple[str, bytes]] = {}

    def close(self) -> None:
        self._pack.close()
        self._file.close()

    # ---- index lookups
    def _sha_at(self, i: int) -> bytes:
        s = self._sha_start + 20 * i
        return self._idx[s:s + 20]

    def _offset_at(self, i: int) -> int:
        (off,) = struct.unpack(">I", self._idx[self._off_start + 4 * i:self._off_start + 4 * i + 4])
        if off & 0x80000000:
            j = off & 0x7FFFFFFF
            (off,) = struct.unpack(">Q", self._idx[self._big_start + 8 * j:self._big_start + 8 * j + 8])
        return off

    def _bounds(self, first_byte: int) -> tuple[int, int]:
        lo = self._fanout[first_byte - 1] if first_byte else 0
        return lo, self._fanout[first_byte]

    def find(self, sha_bin: bytes):
        """Return the pack offset of an object, or None."""
        lo, hi = self._bounds(sha_bin[0])
        while lo < hi:
            mid = (lo + hi) // 2
            cur = self._sha_at(mid)
            if cur == sha_bin:
                return self._offset_at(mid)
            if cur < sha_bin:
                lo = mid + 1
            else:
                hi = mid
        return None

    def prefix_matches(self, hex_prefix: str) -> list[str]:
        even = hex_prefix[:len(hex_prefix) // 2 * 2]
        lo_bin = bytes.fromhex(even) if even else b""
        first = lo_bin[0] if lo_bin else 0
        lo, hi = self._bounds(first) if lo_bin else (0, self.count)
        found = []
        for i in range(lo, hi):
            h = self._sha_at(i).hex()
            if h.startswith(hex_prefix):
                found.append(h)
        return found

    def all_shas(self):
        for i in range(self.count):
            yield self._sha_at(i).hex()

    # ---- object reading
    def _inflate(self, pos: int, size: int) -> bytes:
        d = zlib.decompressobj()
        chunks = []
        while not d.eof:
            chunk = self._pack[pos:pos + 65536]
            if not chunk:
                raise GitError("corrupt pack: truncated zlib stream")
            pos += len(chunk)
            chunks.append(d.decompress(chunk))
        data = b"".join(chunks)
        if len(data) != size:
            raise GitError("corrupt pack: object size mismatch")
        return data

    def read_at(self, offset: int) -> tuple[str, bytes]:
        """Read the object at ``offset``, resolving OFS_DELTA / REF_DELTA chains."""
        try:
            return self._read_at(offset)
        except (IndexError, ValueError, zlib.error, struct.error):
            raise GitError(f"corrupt pack: {os.path.basename(self.pack_path)} (bad data at offset {offset})") from None

    def _read_at(self, offset: int) -> tuple[str, bytes]:
        if offset in self._cache:
            return self._cache[offset]
        pack = self._pack
        chain: list[bytes] = []
        off = offset
        while True:
            pos = off
            c = pack[pos]
            pos += 1
            kind = (c >> 4) & 7
            size = c & 15
            shift = 4
            while c & 0x80:
                c = pack[pos]
                pos += 1
                size |= (c & 0x7F) << shift
                shift += 7
            if kind in _TYPE_NAMES:
                base = (_TYPE_NAMES[kind], self._inflate(pos, size))
                break
            if kind == OFS_DELTA:
                c = pack[pos]
                pos += 1
                back = c & 0x7F
                while c & 0x80:
                    c = pack[pos]
                    pos += 1
                    back = ((back + 1) << 7) | (c & 0x7F)
                chain.append(self._inflate(pos, size))
                off = off - back
            elif kind == REF_DELTA:
                base_sha = bytes(pack[pos:pos + 20])
                pos += 20
                chain.append(self._inflate(pos, size))
                base_off = self.find(base_sha)
                if base_off is None:
                    if self.resolver is None:
                        raise GitError("corrupt pack: REF_DELTA base not found")
                    base = self.resolver(base_sha.hex())
                    break
                off = base_off
            else:
                raise GitError(f"corrupt pack: unknown object type {kind}")
        obj_type, data = base
        for delta in reversed(chain):
            data = apply_delta(data, delta)
        if len(self._cache) > 256:
            self._cache.clear()
        self._cache[offset] = (obj_type, data)
        return obj_type, data

    def read(self, sha_hex: str):
        off = self.find(bytes.fromhex(sha_hex))
        return None if off is None else self.read_at(off)
