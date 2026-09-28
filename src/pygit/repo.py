"""The Repository object: ties together objects, refs, index and config."""
from __future__ import annotations

import os
import time

from . import refs as R
from .index import Index, read_index, write_index
from .objects import (EMPTY_TREE, MODE_TREE, Commit, Ident, Tag, entry_type, parse_tree,
                      serialize_tree)
from .odb import ObjectDB
from .util import Config, GitError, global_config_path, local_tz, parse_date


class Repo:
    def __init__(self, root: str, gitdir: str):
        self.root = root
        self.gitdir = gitdir
        self.odb = ObjectDB(os.path.join(gitdir, "objects"))
        self._config: Config | None = None
        self._commits: dict[str, Commit] = {}

    # ---- discovery / creation
    @classmethod
    def discover(cls, start: str | None = None) -> "Repo":
        cur = os.path.abspath(start or os.getcwd())
        while True:
            dot = os.path.join(cur, ".git")
            if os.path.isdir(dot):
                return cls(cur, dot)
            if os.path.isfile(dot):
                with open(dot, encoding="utf-8") as f:
                    text = f.read().strip()
                if text.startswith("gitdir:"):
                    target = text[7:].strip()
                    return cls(cur, os.path.normpath(os.path.join(cur, target)))
            parent = os.path.dirname(cur)
            if parent == cur:
                raise GitError("not a git repository (or any of the parent directories): .git")
            cur = parent

    @classmethod
    def init(cls, path: str, branch: str = "main") -> tuple["Repo", bool]:
        if not R.valid_ref_name("refs/heads/" + branch):
            raise GitError(f"invalid initial branch name: '{branch}'")
        path = os.path.abspath(path)
        gitdir = os.path.join(path, ".git")
        existed = os.path.exists(os.path.join(gitdir, "HEAD"))
        for sub in ("objects/info", "objects/pack", "refs/heads", "refs/tags", "info"):
            os.makedirs(os.path.join(gitdir, sub), exist_ok=True)
        if not existed:
            with open(os.path.join(gitdir, "HEAD"), "w") as f:
                f.write(f"ref: refs/heads/{branch}\n")
            with open(os.path.join(gitdir, "config"), "w") as f:
                f.write("[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n"
                        "\tbare = false\n\tlogallrefupdates = true\n")
            with open(os.path.join(gitdir, "description"), "w") as f:
                f.write("Unnamed repository; edit this file to name the repository.\n")
        return cls(path, gitdir), existed

    # ---- config / identity
    @property
    def config(self) -> Config:
        if self._config is None:
            self._config = Config([global_config_path(), os.path.join(self.gitdir, "config")])
        return self._config

    def identity(self, kind: str) -> Ident:
        env = os.environ
        name = env.get(f"GIT_{kind}_NAME") or self.config.get("user.name")
        email = env.get(f"GIT_{kind}_EMAIL") or self.config.get("user.email") or env.get("EMAIL")
        if not name or not email:
            raise GitError(
                "Author identity unknown\n\n*** Please tell me who you are.\n\nRun\n\n"
                '  pygit config user.email "you@example.com"\n'
                '  pygit config user.name "Your Name"\n\nto set your identity for this repository.')
        date = env.get(f"GIT_{kind}_DATE")
        ts, tz = parse_date(date) if date else (int(time.time()), local_tz())
        clean = lambda s: s.replace("<", "").replace(">", "").replace("\n", " ").strip()
        return Ident(clean(name), clean(email), ts, tz)

    # ---- paths
    def rel(self, arg: str) -> str:
        """Turn a path given on the command line (relative to cwd) into a repo-relative one."""
        full = os.path.normpath(os.path.join(os.getcwd(), arg))
        rel = os.path.relpath(full, self.root)
        if rel == ".":
            return ""
        if rel == ".." or rel.startswith(".." + os.sep):
            raise GitError(f"'{arg}' is outside repository", 128)
        return rel.replace(os.sep, "/")

    def full(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split("/")) if rel else self.root

    # ---- objects
    def read_object(self, sha: str):
        return self.odb.read(sha)

    def read_commit(self, sha: str) -> Commit:
        if sha not in self._commits:
            t, data = self.odb.read(sha)
            if t != "commit":
                raise GitError(f"object {sha} is a {t}, not a commit")
            self._commits[sha] = Commit.parse(data)
        return self._commits[sha]

    def resolve(self, spec: str) -> str:
        from .revparse import resolve
        return resolve(self, spec)

    def resolve_commit(self, spec: str) -> str:
        from .revparse import peel
        return peel(self, self.resolve(spec), "commit")

    def peel(self, sha: str, want: str | None = None) -> str:
        from .revparse import peel
        return peel(self, sha, want)

    def flatten_tree(self, tree_sha: str, prefix: str = "", out=None) -> dict[str, tuple[int, str]]:
        """Map every path in a tree (recursively) to ``(mode, sha)``."""
        out = {} if out is None else out
        t, data = self.odb.read(tree_sha)
        if t != "tree":
            raise GitError(f"object {tree_sha} is a {t}, not a tree")
        for e in parse_tree(data):
            if e.mode == MODE_TREE:
                self.flatten_tree(e.sha, prefix + e.name + "/", out)
            else:
                out[prefix + e.name] = (e.mode, e.sha)
        return out

    def commit_tree_flat(self, commit_sha: str | None) -> dict[str, tuple[int, str]]:
        if not commit_sha:
            return {}
        return self.flatten_tree(self.read_commit(commit_sha).tree)

    # ---- HEAD
    def head_state(self) -> tuple[str | None, str | None]:
        """Return ``(branch_ref, commit_sha)``. ``branch_ref`` is None when HEAD is detached."""
        target = R.read_symref(self.gitdir, "HEAD")
        if target is not None:
            return target, R.read_ref(self.gitdir, target)
        return None, R.read_ref(self.gitdir, "HEAD")

    def head_sha(self) -> str | None:
        return self.head_state()[1]

    def current_branch(self) -> str | None:
        ref, _ = self.head_state()
        return ref[len("refs/heads/"):] if ref and ref.startswith("refs/heads/") else None

    def set_head_commit(self, sha: str) -> None:
        ref, _ = self.head_state()
        if ref:
            R.write_ref(self.gitdir, ref, sha)
        else:
            R.write_detached_head(self.gitdir, sha)

    # ---- index
    @property
    def index_path(self) -> str:
        return os.path.join(self.gitdir, "index")

    def load_index(self) -> Index:
        return read_index(self.index_path)

    def save_index(self, index: Index) -> None:
        write_index(self.index_path, index)

    def write_tree(self, index: Index) -> str:
        """Build tree objects from the index and return the root tree id."""
        root: dict = {}
        for path, e in index.entries.items():
            parts = path.split("/")
            d = root
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            d[parts[-1]] = e

        def write(d: dict) -> str:
            items = []
            for name, v in d.items():
                if isinstance(v, dict):
                    items.append((MODE_TREE, name, write(v)))
                else:
                    items.append((v.mode, name, v.sha))
            return self.odb.write("tree", serialize_tree(items))

        return write(root)

    # ---- writing commits / tags
    def write_commit(self, tree: str, parents, author: Ident, committer: Ident, message: str) -> str:
        c = Commit(tree, parents, str(author), str(committer), message)
        return self.odb.write("commit", c.serialize())

    def write_tag(self, name: str, target: str, tagger: Ident, message: str) -> str:
        t, _ = self.odb.read(target)
        return self.odb.write("tag", Tag(target, t, name, str(tagger), message).serialize())

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        seen, stack = set(), [descendant]
        while stack:
            sha = stack.pop()
            if sha == ancestor:
                return True
            if sha in seen:
                continue
            seen.add(sha)
            stack.extend(self.read_commit(sha).parents)
        return False


__all__ = ["Repo", "EMPTY_TREE", "entry_type"]
