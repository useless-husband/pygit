"""Turn revision expressions (HEAD~2, main^, abc123, v1^{tree}, HEAD:path) into object ids."""
from __future__ import annotations

import re

from . import refs as R
from .objects import Commit, Tag, parse_tree, MODE_TREE
from .util import GitError

_HEX = re.compile(r"^[0-9a-f]{4,40}$")
_SUFFIX = re.compile(r"\^\{([a-z]*)\}|\^(\d*)|~(\d*)")
_SUFFIXES = re.compile(r"^(?:\^\{[a-z]*\}|\^\d*|~\d*)*$")


def peel(repo, sha: str, want: str | None = None) -> str:
    """Follow tag objects (and commit -> tree when ``want == 'tree'``) until reaching ``want``.

    ``want=None`` means "strip all tag layers".
    """
    while True:
        t, data = repo.odb.read(sha)
        if t == want:
            return sha
        if t == "tag":
            sha = Tag.parse(data).object
        elif t == "commit" and want == "tree":
            sha = Commit.parse(data).tree
        elif want is None:
            return sha
        else:
            raise GitError(f"object {sha} is a {t}, not a {want}")


def _resolve_base(repo, name: str, spec: str) -> str:
    odb = repo.odb
    if len(name) == 40 and odb.exists(name):
        return name
    gd = repo.gitdir
    candidates = []
    if name in ("HEAD", "@"):
        candidates.append("HEAD")
    elif name.startswith("refs/"):
        candidates.append(name)
    candidates += [f"refs/{name}", f"refs/tags/{name}", f"refs/heads/{name}",
                   f"refs/remotes/{name}", f"refs/remotes/{name}/HEAD"]
    for c in candidates:
        if not R.valid_ref_name(c) and c != "HEAD":
            continue
        sha = R.read_ref(gd, c)
        if sha:
            return sha
    if name in ("HEAD", "@"):
        raise GitError(f"Not a valid object name '{spec}' (unknown revision; does this branch have any commits yet?)")
    if _HEX.match(name):
        found = odb.find_prefix(name)
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise GitError(f"short object ID {name} is ambiguous")
    raise GitError(f"Not a valid object name '{spec}' (unknown revision)")


def _lookup_path(repo, tree_sha: str, path: str, spec: str) -> str:
    sha = tree_sha
    for part in [p for p in path.split("/") if p]:
        t, data = repo.odb.read(sha)
        if t != "tree":
            raise GitError(f"path '{path}' does not exist in '{spec}'")
        for e in parse_tree(data):
            if e.name == part:
                sha = e.sha
                break
        else:
            raise GitError(f"path '{path}' does not exist in '{spec}'")
    return sha


def resolve(repo, spec: str) -> str:
    """Resolve ``spec`` to a 40-hex object id (annotated tags are *not* peeled)."""
    if ":" in spec and not spec.startswith(":"):
        rev, path = spec.split(":", 1)
        tree = peel(repo, resolve(repo, rev or "HEAD"), "tree")
        return _lookup_path(repo, tree, path, rev or "HEAD")
    m = re.match(r"^([^\^~]*)(.*)$", spec)
    base, rest = m.group(1), m.group(2)
    if not _SUFFIXES.match(rest):
        raise GitError(f"Not a valid object name '{spec}' (unknown revision)")
    if not base:
        if not rest:
            raise GitError("empty revision")
        base = "HEAD"
    sha = _resolve_base(repo, base, spec)
    for tok in _SUFFIX.finditer(rest):
        peel_type, parent_n, back_n = tok.groups()
        if peel_type is not None:
            sha = peel(repo, sha, peel_type or None)
        elif parent_n is not None:
            n = int(parent_n) if parent_n else 1
            commit_sha = peel(repo, sha, "commit")
            if n == 0:
                sha = commit_sha
            else:
                parents = repo.read_commit(commit_sha).parents
                if n > len(parents):
                    raise GitError(f"revision '{spec}' does not exist: commit has no parent #{n}")
                sha = parents[n - 1]
        else:
            n = int(back_n) if back_n else 1
            sha = peel(repo, sha, "commit")
            for _ in range(n):
                parents = repo.read_commit(sha).parents
                if not parents:
                    raise GitError(f"revision '{spec}' does not exist: ran out of ancestors")
                sha = parents[0]
    return sha


__all__ = ["resolve", "peel", "MODE_TREE"]
