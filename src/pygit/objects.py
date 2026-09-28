"""Git object formats: blob, tree, commit and tag."""
from __future__ import annotations

import hashlib
import re
from typing import NamedTuple

from .util import GitError

OBJECT_TYPES = ("blob", "tree", "commit", "tag")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

MODE_TREE = 0o040000
MODE_FILE = 0o100644
MODE_EXEC = 0o100755
MODE_LINK = 0o120000
MODE_GITLINK = 0o160000


def encode_object(obj_type: str, data: bytes) -> bytes:
    """Return the on-disk (uncompressed) form: ``<type> <size>\\0<data>``."""
    return f"{obj_type} {len(data)}".encode() + b"\0" + data


def hash_bytes(obj_type: str, data: bytes) -> str:
    """SHA-1 of an object, as 40 hex characters."""
    h = hashlib.sha1()
    h.update(f"{obj_type} {len(data)}".encode() + b"\0")
    h.update(data)
    return h.hexdigest()


def decode_object(raw: bytes) -> tuple[str, bytes]:
    nul = raw.find(b"\0")
    if nul < 0:
        raise GitError("corrupt object: missing header")
    try:
        obj_type, size = raw[:nul].decode().split(" ")
        size = int(size)
    except ValueError:
        raise GitError("corrupt object: bad header") from None
    data = raw[nul + 1:]
    if obj_type not in OBJECT_TYPES:
        raise GitError(f"corrupt object: unknown type '{obj_type}'")
    if size != len(data):
        raise GitError(f"corrupt object: size mismatch (header says {size}, got {len(data)})")
    return obj_type, data


# ----------------------------------------------------------------- tree

class TreeEntry(NamedTuple):
    mode: int
    name: str
    sha: str


def parse_tree(data: bytes) -> list[TreeEntry]:
    entries = []
    pos = 0
    while pos < len(data):
        sp = data.find(b" ", pos)
        nul = data.find(b"\0", sp)
        if sp < 0 or nul < 0 or nul + 21 > len(data):
            raise GitError("corrupt tree object")
        mode = int(data[pos:sp], 8)
        name = data[sp + 1:nul].decode("utf-8", "surrogateescape")
        entries.append(TreeEntry(mode, name, data[nul + 1:nul + 21].hex()))
        pos = nul + 21
    return entries


def _tree_sort_key(entry) -> bytes:
    name = entry[1].encode("utf-8", "surrogateescape")
    return name + b"/" if entry[0] == MODE_TREE else name


def serialize_tree(entries) -> bytes:
    """Serialize ``(mode, name, sha)`` tuples. Directories sort as if named ``name/``."""
    out = []
    for mode, name, sha in sorted(entries, key=_tree_sort_key):
        out.append(b"%o %s\0%s" % (mode, name.encode("utf-8", "surrogateescape"), bytes.fromhex(sha)))
    return b"".join(out)


def entry_type(mode: int) -> str:
    if mode == MODE_TREE:
        return "tree"
    if mode == MODE_GITLINK:
        return "commit"
    return "blob"


# --------------------------------------------------------------- ident

class Ident(NamedTuple):
    name: str
    email: str
    ts: int
    tz: str

    def __str__(self) -> str:
        return f"{self.name} <{self.email}> {self.ts} {self.tz}"

    @property
    def who(self) -> str:
        return f"{self.name} <{self.email}>"


_IDENT = re.compile(r"^(.*?) ?<(.*?)> (\d+) ([+-]\d{4})$")


def parse_ident(text: str) -> Ident:
    m = _IDENT.match(text)
    if not m:
        return Ident(text, "", 0, "+0000")
    return Ident(m.group(1), m.group(2), int(m.group(3)), m.group(4))


# -------------------------------------------------------- commit / tag

def _parse_headers(data: bytes):
    """Split an object into ([(key, value)], message). Continuation lines start with a space."""
    idx = data.find(b"\n\n")
    if idx < 0:
        head, msg = data.rstrip(b"\n"), b""
    else:
        head, msg = data[:idx], data[idx + 2:]
    headers: list[list[str]] = []
    for line in head.decode("utf-8", "surrogateescape").split("\n"):
        if line.startswith(" ") and headers:
            headers[-1][1] += "\n" + line[1:]
        else:
            key, _, value = line.partition(" ")
            headers.append([key, value])
    return [(k, v) for k, v in headers], msg.decode("utf-8", "surrogateescape")


def _emit_header(key: str, value: str) -> str:
    return f"{key} {value.replace(chr(10), chr(10) + ' ')}\n"


class Commit:
    def __init__(self, tree, parents, author, committer, message, extra=()):
        self.tree = tree
        self.parents = list(parents)
        self.author = author        # "Name <email> ts tz"
        self.committer = committer
        self.message = message
        self.extra = list(extra)    # e.g. gpgsig, encoding

    @classmethod
    def parse(cls, data: bytes) -> "Commit":
        headers, message = _parse_headers(data)
        tree, parents, author, committer, extra = None, [], "", "", []
        for k, v in headers:
            if k == "tree":
                tree = v
            elif k == "parent":
                parents.append(v)
            elif k == "author":
                author = v
            elif k == "committer":
                committer = v
            else:
                extra.append((k, v))
        if tree is None:
            raise GitError("corrupt commit: no tree line")
        return cls(tree, parents, author, committer, message, extra)

    def serialize(self) -> bytes:
        out = _emit_header("tree", self.tree)
        for p in self.parents:
            out += _emit_header("parent", p)
        out += _emit_header("author", self.author) + _emit_header("committer", self.committer)
        for k, v in self.extra:
            out += _emit_header(k, v)
        out += "\n" + self.message
        return out.encode("utf-8", "surrogateescape")

    @property
    def author_ident(self) -> Ident:
        return parse_ident(self.author)

    @property
    def committer_ident(self) -> Ident:
        return parse_ident(self.committer)

    @property
    def subject(self) -> str:
        lines = []
        for line in self.message.split("\n"):
            if not line.strip():
                if lines:
                    break
                continue
            lines.append(line.strip())
        return " ".join(lines)


class Tag:
    def __init__(self, object_sha, object_type, name, tagger, message):
        self.object = object_sha
        self.type = object_type
        self.name = name
        self.tagger = tagger
        self.message = message

    @classmethod
    def parse(cls, data: bytes) -> "Tag":
        headers, message = _parse_headers(data)
        d = dict(headers)
        if "object" not in d or "type" not in d:
            raise GitError("corrupt tag object")
        return cls(d["object"], d["type"], d.get("tag", ""), d.get("tagger", ""), message)

    def serialize(self) -> bytes:
        out = _emit_header("object", self.object) + _emit_header("type", self.type)
        out += _emit_header("tag", self.name)
        if self.tagger:
            out += _emit_header("tagger", self.tagger)
        out += "\n" + self.message
        return out.encode("utf-8", "surrogateescape")


def clean_message(message: str) -> str:
    """Like git's default cleanup: strip trailing spaces, squeeze blank lines, end with newline."""
    lines = [ln.rstrip() for ln in message.split("\n")]
    out: list[str] = []
    for ln in lines:
        if not ln and (not out or not out[-1]):
            continue
        out.append(ln)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out) + "\n" if out else ""
