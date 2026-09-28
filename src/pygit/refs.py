"""References: branches, tags, HEAD and packed-refs."""
from __future__ import annotations

import os
import re

from .util import GitError

_HEX40 = re.compile(r"^[0-9a-f]{40}$")


def valid_ref_name(name: str) -> bool:
    if not name or name.startswith("-") or name.startswith("/") or name.endswith("/") or name.endswith("."):
        return False
    if ".." in name or "@{" in name or "//" in name or name == "@":
        return False
    if any(c in name for c in " ~^:?*[\\\x7f") or any(ord(c) < 32 for c in name):
        return False
    for part in name.split("/"):
        if part.startswith(".") or part.endswith(".lock"):
            return False
    return True


def read_packed(gitdir: str) -> dict[str, str]:
    refs: dict[str, str] = {}
    try:
        with open(os.path.join(gitdir, "packed-refs"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line[0] in "#^":
                    continue
                sha, _, name = line.partition(" ")
                refs[name] = sha
    except FileNotFoundError:
        pass
    return refs


def read_symref(gitdir: str, name: str):
    """Return the target of a symbolic ref (``ref: X``), else None."""
    try:
        with open(os.path.join(gitdir, name), encoding="utf-8") as f:
            content = f.read().strip()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None
    return content[5:].strip() if content.startswith("ref: ") else None


def read_ref(gitdir: str, name: str, _depth: int = 0):
    """Resolve a ref (following symbolic refs) to a 40-hex sha, or None if it does not exist."""
    if _depth > 10:
        raise GitError(f"symbolic ref loop at '{name}'")
    path = os.path.join(gitdir, name)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            content = f.read().strip()
        if content.startswith("ref: "):
            return read_ref(gitdir, content[5:].strip(), _depth + 1)
        if _HEX40.match(content):
            return content
        raise GitError(f"ref '{name}' is corrupt")
    return read_packed(gitdir).get(name)


def list_refs(gitdir: str, prefix: str = "refs/") -> dict[str, str]:
    refs = {n: s for n, s in read_packed(gitdir).items() if n.startswith(prefix)}
    base = os.path.join(gitdir, prefix)
    if os.path.isdir(base):
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                if fn.endswith(".lock"):
                    continue
                name = os.path.relpath(os.path.join(dirpath, fn), gitdir).replace(os.sep, "/")
                sha = read_ref(gitdir, name)
                if sha:
                    refs[name] = sha
    return dict(sorted(refs.items()))


def _final_name(gitdir: str, name: str) -> str:
    for _ in range(10):
        target = read_symref(gitdir, name)
        if target is None:
            return name
        name = target
    raise GitError(f"symbolic ref loop at '{name}'")


def _atomic_write(path: str, text: str) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lock = path + ".lock"
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except (FileExistsError, NotADirectoryError, IsADirectoryError) as e:
        raise GitError(f"cannot lock ref '{os.path.basename(path)}': {e.strerror or e}") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(lock, path)
    except BaseException:
        if os.path.exists(lock):
            os.remove(lock)
        raise


def write_ref(gitdir: str, name: str, sha: str) -> None:
    """Point ``name`` (following symbolic refs such as HEAD) at ``sha``."""
    name = _final_name(gitdir, name)
    if name != "HEAD" and not (name.startswith("refs/") and valid_ref_name(name)):
        raise GitError(f"'{name}' is not a valid reference name")
    if not os.path.isfile(os.path.join(gitdir, name)):
        # a ref like refs/heads/a/b cannot coexist with refs/heads/a
        parts = name.split("/")
        for i in range(2, len(parts)):
            if read_ref(gitdir, "/".join(parts[:i])):
                raise GitError(f"cannot create '{name}': '{'/'.join(parts[:i])}' exists")
    _atomic_write(os.path.join(gitdir, name), sha + "\n")


def write_symref(gitdir: str, name: str, target: str) -> None:
    _atomic_write(os.path.join(gitdir, name), f"ref: {target}\n")


def write_detached_head(gitdir: str, sha: str) -> None:
    _atomic_write(os.path.join(gitdir, "HEAD"), sha + "\n")


def delete_ref(gitdir: str, name: str) -> None:
    path = os.path.join(gitdir, name)
    if os.path.isfile(path):
        os.remove(path)
    packed = os.path.join(gitdir, "packed-refs")
    if os.path.exists(packed):
        with open(packed, encoding="utf-8") as f:
            lines = f.read().splitlines()
        kept, skip_peeled = [], False
        for line in lines:
            if line.startswith("^"):
                if skip_peeled:
                    continue
            else:
                skip_peeled = (not line.startswith("#")) and line.partition(" ")[2] == name
                if skip_peeled:
                    continue
            kept.append(line)
        with open(packed, "w", encoding="utf-8") as f:
            f.write("\n".join(kept) + "\n")
    d = os.path.dirname(path)
    stop = os.path.join(gitdir, "refs")
    while d.startswith(stop) and d != stop and not os.listdir(d) and \
            os.path.basename(d) not in ("heads", "tags"):
        os.rmdir(d)
        d = os.path.dirname(d)
