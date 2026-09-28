"""Working-tree operations: stat cache, staging files, status and checkout."""
from __future__ import annotations

import os
import stat as st_mod
from dataclasses import dataclass, field

from .ignore import IgnoreMatcher
from .index import Index, IndexEntry
from .objects import MODE_EXEC, MODE_FILE, MODE_GITLINK, MODE_LINK, hash_bytes
from .util import GitError, pkey

MASK = 0xFFFFFFFF


def mode_from_stat(st) -> int | None:
    if st_mod.S_ISLNK(st.st_mode):
        return MODE_LINK
    if st_mod.S_ISREG(st.st_mode):
        return MODE_EXEC if st.st_mode & 0o100 else MODE_FILE
    return None


def read_worktree_file(full: str, mode: int) -> bytes:
    if mode == MODE_LINK:
        return os.fsencode(os.readlink(full))
    with open(full, "rb") as f:
        return f.read()


def entry_from_stat(path: str, st, sha: str, mode: int) -> IndexEntry:
    return IndexEntry(
        path, sha, mode, size=st.st_size & MASK,
        ctime_s=st.st_ctime_ns // 10**9, ctime_ns=st.st_ctime_ns % 10**9,
        mtime_s=st.st_mtime_ns // 10**9, mtime_ns=st.st_mtime_ns % 10**9,
        dev=st.st_dev & MASK, ino=st.st_ino & MASK, uid=st.st_uid & MASK, gid=st.st_gid & MASK)


def stat_matches(entry: IndexEntry, st, index_mtime_ns: int) -> bool:
    """True if the cached stat data says the file is unchanged (and not 'racily clean')."""
    if entry.size != (st.st_size & MASK):
        return False
    m = st.st_mtime_ns
    if entry.mtime_s != m // 10**9 or entry.mtime_ns != m % 10**9:
        return False
    if entry.ino != (st.st_ino & MASK):
        return False
    return m < index_mtime_ns


def check_entry(root: str, entry: IndexEntry, index_mtime_ns: int) -> str | None:
    """Compare one index entry with the work tree: None (clean), 'M', 'D' or 'T'."""
    if entry.mode == MODE_GITLINK:
        return None
    full = os.path.join(root, *entry.path.split("/"))
    try:
        st = os.lstat(full)
    except (FileNotFoundError, NotADirectoryError):
        return "D"
    mode = mode_from_stat(st)
    if mode is None:
        return "D"
    if (mode >> 12) != (entry.mode >> 12):
        return "T"
    if stat_matches(entry, st, index_mtime_ns):
        return "M" if mode != entry.mode else None
    sha = hash_bytes("blob", read_worktree_file(full, mode))
    return "M" if sha != entry.sha or mode != entry.mode else None


def stage_path(repo, index: Index, rel: str) -> None:
    """Hash a work-tree file, store it as a blob and update its index entry."""
    full = repo.full(rel)
    st = os.lstat(full)
    mode = mode_from_stat(st)
    if mode is None:
        raise GitError(f"unable to index '{rel}': not a regular file or symlink", 128)
    old = index.entries.get(rel)
    if old and old.mode == mode and stat_matches(old, st, index.mtime_ns):
        return
    sha = repo.odb.write("blob", read_worktree_file(full, mode))
    index.entries[rel] = entry_from_stat(rel, st, sha, mode)
    parts = rel.split("/")
    for i in range(1, len(parts)):          # a file replaces a directory of the same name
        index.entries.pop("/".join(parts[:i]), None)


def prune_empty_dirs(root: str, rel: str) -> None:
    d = os.path.dirname(os.path.join(root, *rel.split("/")))
    while d != root and d.startswith(root):
        try:
            os.rmdir(d)
        except OSError:
            break
        d = os.path.dirname(d)


def remove_worktree_file(root: str, rel: str) -> None:
    full = os.path.join(root, *rel.split("/"))
    try:
        os.remove(full)
    except (FileNotFoundError, NotADirectoryError):
        pass
    prune_empty_dirs(root, rel)


def _is_nested_repo(path: str) -> bool:
    return os.path.exists(os.path.join(path, ".git"))


def walk_files(root: str, ignore: IgnoreMatcher, dirrel: str = "") -> list[str]:
    """All non-ignored files and symlinks below ``dirrel`` (repo-relative, ``/`` separated)."""
    out: list[str] = []

    def rec(rel: str) -> None:
        try:
            entries = sorted(os.scandir(os.path.join(root, *rel.split("/")) if rel else root),
                             key=lambda e: e.name)
        except (FileNotFoundError, NotADirectoryError):
            return
        for e in entries:
            if e.name == ".git":
                continue
            r = f"{rel}/{e.name}" if rel else e.name
            if e.is_dir(follow_symlinks=False):
                if _is_nested_repo(e.path) or ignore.is_ignored(r, True):
                    continue
                rec(r)
            elif not ignore.is_ignored(r, False):
                out.append(r)

    rec(dirrel)
    return out


def find_untracked(root: str, index: Index, ignore: IgnoreMatcher) -> list[str]:
    """Untracked paths like ``git status``: whole untracked directories are shown as ``dir/``."""
    tracked = index.entries
    tracked_dirs = set()
    for p in tracked:
        parts = p.split("/")
        for i in range(1, len(parts)):
            tracked_dirs.add("/".join(parts[:i]))
    out: list[str] = []

    def has_untracked(rel: str) -> bool:
        for e in sorted(os.scandir(os.path.join(root, *rel.split("/"))), key=lambda e: e.name):
            if e.name == ".git":
                continue
            r = f"{rel}/{e.name}"
            if e.is_dir(follow_symlinks=False):
                if _is_nested_repo(e.path):
                    return True
                if not ignore.is_ignored(r, True) and has_untracked(r):
                    return True
            elif not ignore.is_ignored(r, False):
                return True
        return False

    def rec(rel: str) -> None:
        for e in sorted(os.scandir(os.path.join(root, *rel.split("/")) if rel else root),
                        key=lambda e: e.name):
            if e.name == ".git":
                continue
            r = f"{rel}/{e.name}" if rel else e.name
            if e.is_dir(follow_symlinks=False):
                if r in tracked_dirs:
                    rec(r)
                elif _is_nested_repo(e.path):
                    out.append(r + "/")
                elif not ignore.is_ignored(r, True) and has_untracked(r):
                    out.append(r + "/")
            elif r not in tracked and not ignore.is_ignored(r, False):
                out.append(r)

    rec("")
    out.sort(key=pkey)
    return out


@dataclass
class Status:
    staged: dict = field(default_factory=dict)      # path -> 'A' | 'M' | 'D' | 'T'
    unstaged: dict = field(default_factory=dict)    # path -> 'M' | 'D' | 'T'
    untracked: list = field(default_factory=list)


def compute_status(repo, index: Index | None = None) -> Status:
    index = index or repo.load_index()
    head = repo.commit_tree_flat(repo.head_sha())
    st = Status()
    current = {p: (e.mode, e.sha) for p, e in index.entries.items()}
    for path in set(head) | set(current):
        h, c = head.get(path), current.get(path)
        if h == c:
            continue
        if h is None:
            st.staged[path] = "A"
        elif c is None:
            st.staged[path] = "D"
        else:
            st.staged[path] = "T" if (h[0] >> 12) != (c[0] >> 12) else "M"
    for path, entry in index.entries.items():
        r = check_entry(repo.root, entry, index.mtime_ns)
        if r:
            st.unstaged[path] = r
    st.untracked = find_untracked(repo.root, index, IgnoreMatcher(repo.root, repo.gitdir))
    return st


# ------------------------------------------------------------- checkout

def write_worktree_file(root: str, rel: str, mode: int, data: bytes) -> None:
    full = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    if os.path.lexists(full):
        if os.path.isdir(full) and not os.path.islink(full):
            raise GitError(f"cannot write '{rel}': a directory is in the way", 128)
        os.remove(full)
    if mode == MODE_LINK:
        os.symlink(os.fsdecode(data), full)
        return
    with open(full, "wb") as f:
        f.write(data)
    umask = os.umask(0)
    os.umask(umask)
    os.chmod(full, (0o777 if mode == MODE_EXEC else 0o666) & ~umask)


def _dir_being_replaced(repo, index, path: str, old: dict, new: dict) -> bool:
    """True if ``path`` is a directory whose tracked files are all about to disappear."""
    full = repo.full(path)
    if not os.path.isdir(full) or os.path.islink(full):
        return False
    prefix = path + "/"
    inside = [p for p in index.entries if p.startswith(prefix)]
    return bool(inside) and all(p in old and p not in new for p in inside)


def checkout_tree(repo, head_sha: str | None, target_sha: str, force: bool = False) -> None:
    """Move work tree and index from ``head_sha``'s tree to ``target_sha``'s tree.

    Refuses if a local change or an untracked file would be overwritten.
    """
    index = repo.load_index()
    old = repo.commit_tree_flat(head_sha)
    new = repo.commit_tree_flat(target_sha)
    changed = sorted((p for p in set(old) | set(new) if old.get(p) != new.get(p)), key=pkey)
    if not force:
        dirty, untracked = [], []
        for p in changed:
            entry = index.entries.get(p)
            if entry is None:
                if p in old:
                    dirty.append(p)                    # staged deletion
                elif os.path.lexists(repo.full(p)) and not _dir_being_replaced(repo, index, p, old, new):
                    untracked.append(p)
            elif (entry.mode, entry.sha) != old.get(p) or \
                    check_entry(repo.root, entry, index.mtime_ns) is not None:
                dirty.append(p)
        if dirty:
            raise GitError("Your local changes to the following files would be overwritten by checkout:\n"
                           + "".join(f"\t{p}\n" for p in dirty)
                           + "Please commit your changes or stash them before you switch branches.\nAborting", 1)
        if untracked:
            raise GitError("The following untracked working tree files would be overwritten by checkout:\n"
                           + "".join(f"\t{p}\n" for p in untracked)
                           + "Please move or remove them before you switch branches.\nAborting", 1)
    for p in changed:
        if p not in new:
            remove_worktree_file(repo.root, p)
            index.entries.pop(p, None)
    for p in changed:
        if p in new:
            mode, sha = new[p]
            _, data = repo.odb.read(sha)
            # a directory being replaced by a file, or vice versa
            for i in range(1, len(p.split("/"))):
                index.entries.pop("/".join(p.split("/")[:i]), None)
            write_worktree_file(repo.root, p, mode, data)
            index.entries[p] = entry_from_stat(p, os.lstat(repo.full(p)), sha, mode)
    repo.save_index(index)
