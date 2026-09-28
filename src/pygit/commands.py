"""Implementations of every pygit sub-command. Each takes the parsed argparse namespace."""
from __future__ import annotations

import heapq
import os
import sys

from . import refs as R
from .diff import format_file_diff
from .ignore import IgnoreMatcher
from .objects import (EMPTY_TREE, MODE_TREE, OBJECT_TYPES, Commit, Tag, clean_message, entry_type,
                      hash_bytes, parse_ident, parse_tree)
from .repo import Repo
from .util import GitError, Config, format_date, global_config_path, pkey, quote_path, set_config
from .worktree import (checkout_tree, check_entry, compute_status, mode_from_stat, prune_empty_dirs,
                       read_worktree_file, remove_worktree_file, stage_path, walk_files)


def out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def err(text: str) -> None:
    sys.stderr.write(text + "\n")


def write_bytes(data: bytes) -> None:
    sys.stdout.flush()
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def paths_arg(a) -> list[str]:
    return list(getattr(a, "paths", []) or []) + list(getattr(a, "dashdash", []) or [])


def q(path: str, z: bool = False, sp: bool = False) -> str:
    return path if z else quote_path(path, sp)


# ------------------------------------------------------------ plumbing

def cmd_init(a) -> int:
    repo, existed = Repo.init(a.directory or ".", a.initial_branch or "main")
    out(f"{'Reinitialized existing' if existed else 'Initialized empty'} Git repository in {repo.gitdir}/")
    return 0


def cmd_hash_object(a) -> int:
    if a.type not in OBJECT_TYPES:
        raise GitError(f"invalid object type \"{a.type}\"")
    repo = Repo.discover() if a.write else None
    sources = []
    if a.stdin:
        sources.append(sys.stdin.buffer.read())
    for p in a.files:
        try:
            with open(p, "rb") as f:
                sources.append(f.read())
        except OSError as e:
            raise GitError(f"could not open '{p}': {e.strerror}") from None
    if not sources:
        raise GitError("nothing to hash: give a file or --stdin", 129)
    for data in sources:
        out(repo.odb.write(a.type, data) if repo else hash_bytes(a.type, data))
    return 0


def _pretty_tree(data: bytes) -> str:
    return "".join(f"{e.mode:06o} {entry_type(e.mode)} {e.sha}\t{q(e.name)}\n" for e in parse_tree(data))


def cmd_cat_file(a) -> int:
    repo = Repo.discover()
    flags = [f for f in ("type", "size", "pretty", "exists") if getattr(a, f)]
    if flags:
        if len(a.objects) != 1 or len(flags) != 1:
            raise GitError("cat-file: use exactly one of -t, -s, -p, -e with one object", 129)
        try:
            sha = repo.resolve(a.objects[0])
            t, data = repo.read_object(sha)
        except GitError:
            if a.exists:
                return 1
            raise
        if a.exists:
            return 0
        if a.type:
            out(t)
        elif a.size:
            out(str(len(data)))
        else:
            write_bytes(_pretty_tree(data).encode("utf-8", "surrogateescape") if t == "tree" else data)
        return 0
    if len(a.objects) != 2:
        raise GitError("usage: pygit cat-file (-t | -s | -p | -e) <object>  or  cat-file <type> <object>", 129)
    want, spec = a.objects
    sha = repo.resolve(spec)
    if want in OBJECT_TYPES:
        sha = repo.peel(sha, want) if want != "tag" else sha
    t, data = repo.read_object(sha)
    if t != want:
        raise GitError(f"object {sha} is a {t}, not a {want}")
    write_bytes(data)
    return 0


def cmd_write_tree(a) -> int:
    repo = Repo.discover()
    out(repo.write_tree(repo.load_index()))
    return 0


def cmd_commit_tree(a) -> int:
    repo = Repo.discover()
    tree = repo.peel(repo.resolve(a.tree), "tree")
    parents = [repo.peel(repo.resolve(p), "commit") for p in a.parent]
    if a.message:
        message = "\n\n".join(a.message) + "\n"
    else:
        message = sys.stdin.read()
    sha = repo.write_commit(tree, parents, repo.identity("AUTHOR"), repo.identity("COMMITTER"), message)
    out(sha)
    return 0


def cmd_update_ref(a) -> int:
    repo = Repo.discover()
    if a.delete:
        name = a.ref
        if R.read_ref(repo.gitdir, name) is None:
            raise GitError(f"ref '{name}' does not exist")
        R.delete_ref(repo.gitdir, name)
        return 0
    if not a.value:
        raise GitError("usage: pygit update-ref [-d] <ref> <new-value>", 129)
    sha = repo.resolve(a.value)
    t, _ = repo.read_object(sha)
    if a.ref.startswith("refs/heads/") and t != "commit":
        raise GitError(f"cannot update ref '{a.ref}': trying to write non-commit object {sha} to branch")
    R.write_ref(repo.gitdir, a.ref, sha)
    return 0


def cmd_symbolic_ref(a) -> int:
    repo = Repo.discover()
    if a.target:
        if not a.target.startswith("refs/") or not R.valid_ref_name(a.target):
            raise GitError(f"refusing to point {a.name} outside of refs/")
        R.write_symref(repo.gitdir, a.name, a.target)
        return 0
    target = R.read_symref(repo.gitdir, a.name)
    if target is None:
        raise GitError(f"ref {a.name} is not a symbolic ref")
    out(target)
    return 0


def cmd_rev_parse(a) -> int:
    if a.git_dir or a.show_toplevel:
        repo = Repo.discover()
        if a.git_dir:
            out(os.path.relpath(repo.gitdir) if not a.show_toplevel else repo.gitdir)
        if a.show_toplevel:
            out(repo.root)
        if not a.revs:
            return 0
    repo = Repo.discover()
    for spec in a.revs:
        sha = repo.resolve(spec)
        out(sha[:7] if a.short else sha)
    return 0


def cmd_ls_files(a) -> int:
    repo = Repo.discover()
    idx = repo.load_index()
    end = "\0" if a.z else "\n"
    for e in idx.sorted_entries():
        if a.stage:
            sys.stdout.write(f"{e.mode:06o} {e.sha} {e.stage}\t{q(e.path, a.z)}{end}")
        else:
            sys.stdout.write(f"{q(e.path, a.z)}{end}")
    return 0


def _ls_tree(repo, tree_sha, recursive, prefix, name_only, z, only_trees=False):
    end = "\0" if z else "\n"
    _, data = repo.read_object(tree_sha)
    for e in parse_tree(data):
        path = prefix + e.name
        if recursive and e.mode == MODE_TREE:
            _ls_tree(repo, e.sha, True, path + "/", name_only, z)
            continue
        if name_only:
            sys.stdout.write(f"{q(path, z)}{end}")
        else:
            sys.stdout.write(f"{e.mode:06o} {entry_type(e.mode)} {e.sha}\t{q(path, z)}{end}")


def cmd_ls_tree(a) -> int:
    repo = Repo.discover()
    tree = repo.peel(repo.resolve(a.tree), "tree")
    _ls_tree(repo, tree, a.recursive, "", a.name_only, a.z)
    return 0


def cmd_config(a) -> int:
    path = global_config_path() if a.use_global else None
    if a.list:
        cfg = Config([global_config_path()] + ([] if a.use_global else [os.path.join(Repo.discover().gitdir, "config")]))
        for k, v in cfg.values.items():
            out(f"{k}={v}")
        return 0
    if not a.key:
        raise GitError("usage: pygit config [--global] <key> [<value>]", 129)
    if a.value is None:
        repo_cfg = Config([global_config_path()]) if a.use_global else Repo.discover().config
        v = repo_cfg.get(a.key)
        if v is None:
            return 1
        out(v)
        return 0
    if path is None:
        path = os.path.join(Repo.discover().gitdir, "config")
    set_config(path, a.key, a.value)
    return 0


# ------------------------------------------------------------ porcelain

def _pathspec_match(path: str, specs: list[str]) -> bool:
    return not specs or any(s == "" or path == s or path.startswith(s + "/") for s in specs)


def cmd_add(a) -> int:
    repo = Repo.discover()
    specs = paths_arg(a)
    if not specs:
        raise GitError("Nothing specified, nothing added.", 1)
    idx = repo.load_index()
    ign = IgnoreMatcher(repo.root, repo.gitdir)
    ignored_errors = []
    for arg in specs:
        rel = repo.rel(arg)
        full = repo.full(rel)
        candidates: set[str] = set()
        exists = os.path.lexists(full)
        if exists and not (os.path.isdir(full) and not os.path.islink(full)):
            if rel not in idx.entries and ign.is_ignored(rel, False):
                ignored_errors.append(rel)
                continue
            candidates.add(rel)
        elif exists:
            candidates.update(walk_files(repo.root, ign, rel))
        prefix = rel + "/" if rel else ""
        under = [p for p in idx.entries if p == rel or p.startswith(prefix)]
        candidates.update(under)
        if not candidates:
            raise GitError(f"pathspec '{arg}' did not match any files")
        for p in sorted(candidates, key=pkey):
            if os.path.lexists(repo.full(p)) and mode_from_stat(os.lstat(repo.full(p))) is not None:
                stage_path(repo, idx, p)
            else:
                idx.entries.pop(p, None)
    repo.save_index(idx)
    if ignored_errors:
        raise GitError("The following paths are ignored by one of your .gitignore files:\n"
                       + "".join(f"{p}\n" for p in ignored_errors) + "hint: remove them from .gitignore to add them", 1)
    return 0


def cmd_rm(a) -> int:
    repo = Repo.discover()
    idx = repo.load_index()
    head = repo.commit_tree_flat(repo.head_sha())
    to_remove: list[str] = []
    for arg in paths_arg(a):
        rel = repo.rel(arg)
        if not rel:
            raise GitError("refusing to remove the repository root")
        matched = [p for p in idx.entries if p == rel or p.startswith(rel + "/")]
        if not matched:
            raise GitError(f"pathspec '{arg}' did not match any files")
        if matched != [rel] and not a.r:
            raise GitError(f"not removing '{arg}' recursively without -r")
        to_remove.extend(matched)
    if not a.force and not a.cached:
        for p in to_remove:
            e = idx.entries[p]
            if check_entry(repo.root, e, idx.mtime_ns) in ("M", "T"):
                raise GitError(f"'{p}' has local modifications\n(use --cached to keep the file, or -f to force removal)", 1)
            if head.get(p) != (e.mode, e.sha):
                raise GitError(f"'{p}' has changes staged in the index\n(use --cached to keep the file, or -f to force removal)", 1)
    for p in sorted(set(to_remove), key=pkey):
        del idx.entries[p]
        if not a.cached:
            remove_worktree_file(repo.root, p)
        out(f"rm '{p}'")
    repo.save_index(idx)
    return 0


def _subject_line(message: str) -> str:
    return message.strip().split("\n", 1)[0]


def cmd_commit(a) -> int:
    repo = Repo.discover()
    idx = repo.load_index()
    if a.all:
        for p, e in list(idx.entries.items()):
            r = check_entry(repo.root, e, idx.mtime_ns)
            if r == "D":
                del idx.entries[p]
            elif r:
                stage_path(repo, idx, p)
        repo.save_index(idx)
    if not a.message:
        raise GitError("Aborting commit: an empty commit message is not allowed (use -m \"message\")", 1)
    message = clean_message("\n\n".join(a.message))
    if not message:
        raise GitError("Aborting commit due to empty commit message.", 1)
    tree = repo.write_tree(idx)
    head = repo.head_sha()
    if not a.allow_empty:
        prev = repo.read_commit(head).tree if head else EMPTY_TREE
        if tree == prev:
            raise GitError("nothing to commit, working tree clean" if not compute_status(repo, idx).untracked
                           else "nothing added to commit but untracked files present (use \"pygit add\" to track)", 1)
    sha = repo.write_commit(tree, [head] if head else [], repo.identity("AUTHOR"),
                            repo.identity("COMMITTER"), message)
    repo.set_head_commit(sha)
    branch = repo.current_branch()
    label = branch or "detached HEAD"
    root = " (root-commit)" if not head else ""
    out(f"[{label}{root} {sha[:7]}] {_subject_line(message)}")
    return 0


_STAGED_LABEL = {"A": "new file:   ", "M": "modified:   ", "D": "deleted:    ", "T": "typechange: "}


def cmd_status(a) -> int:
    repo = Repo.discover()
    st = compute_status(repo)
    if a.porcelain or a.short:
        z = a.z
        lines = []
        for p in sorted(set(st.staged) | set(st.unstaged), key=pkey):
            lines.append(f"{st.staged.get(p, ' ')}{st.unstaged.get(p, ' ')} {q(p, z, True)}")
        lines += [f"?? {q(p, z, True)}" for p in st.untracked]
        if a.short and not a.porcelain and not z:
            branch = repo.current_branch()
            out(f"## {branch or 'HEAD (no branch)'}" + (" (no commits yet)" if not repo.head_sha() else ""))
        sys.stdout.write("".join(ln + ("\0" if z else "\n") for ln in lines))
        return 0
    branch = repo.current_branch()
    if branch:
        out(f"On branch {branch}")
    else:
        out(f"HEAD detached at {(repo.head_sha() or '')[:7]}")
    if not repo.head_sha():
        out("\nNo commits yet")
    if st.staged:
        out("\nChanges to be committed:")
        out('  (use "pygit rm --cached <file>..." to unstage)')
        for p in sorted(st.staged, key=pkey):
            out(f"\t{_STAGED_LABEL[st.staged[p]]}{q(p)}")
    if st.unstaged:
        out("\nChanges not staged for commit:")
        out('  (use "pygit add <file>..." to update what will be committed)')
        for p in sorted(st.unstaged, key=pkey):
            label = {"M": "modified:   ", "D": "deleted:    ", "T": "typechange: "}[st.unstaged[p]]
            out(f"\t{label}{q(p)}")
    if st.untracked:
        out("\nUntracked files:")
        out('  (use "pygit add <file>..." to include in what will be committed)')
        for p in st.untracked:
            out(f"\t{q(p)}")
    out()
    if st.staged:
        pass
    elif st.unstaged:
        out('no changes added to commit (use "pygit add" and/or "pygit commit -a")')
    elif st.untracked:
        out('nothing added to commit but untracked files present (use "pygit add" to track)')
    else:
        out("nothing to commit, working tree clean")
    return 0


def cmd_diff(a) -> int:
    repo = Repo.discover()
    idx = repo.load_index()
    specs = [repo.rel(p) for p in paths_arg(a)]
    chunks = []

    def blob(sha):
        return repo.read_object(sha)[1]

    if a.cached:
        old = repo.commit_tree_flat(repo.head_sha())
        new = {p: (e.mode, e.sha) for p, e in idx.entries.items()}
        for p in sorted(set(old) | set(new), key=pkey):
            if old.get(p) == new.get(p) or not _pathspec_match(p, specs):
                continue
            o = old.get(p)
            n = new.get(p)
            chunks.append(format_file_diff(p, (o[0], o[1], blob(o[1])) if o else None,
                                           (n[0], n[1], blob(n[1])) if n else None))
    else:
        for e in idx.sorted_entries():
            if not _pathspec_match(e.path, specs):
                continue
            r = check_entry(repo.root, e, idx.mtime_ns)
            if r is None:
                continue
            o = (e.mode, e.sha, blob(e.sha))
            n = None
            if r != "D":
                full = repo.full(e.path)
                mode = mode_from_stat(os.lstat(full))
                data = read_worktree_file(full, mode)
                n = (mode, hash_bytes("blob", data), data)
            chunks.append(format_file_diff(e.path, o, n))
    sys.stdout.write("".join(chunks))
    return 0


def iter_commits(repo, start_shas):
    """Yield ``(sha, Commit)`` newest committer date first (like the default ``git log``)."""
    heap, seen, counter = [], set(), 0
    for sha in start_shas:
        if sha not in seen:
            seen.add(sha)
            c = repo.read_commit(sha)
            heapq.heappush(heap, (-c.committer_ident.ts, counter, sha))
            counter += 1
    while heap:
        _, _, sha = heapq.heappop(heap)
        c = repo.read_commit(sha)
        yield sha, c
        for p in c.parents:
            if p not in seen:
                seen.add(p)
                heapq.heappush(heap, (-repo.read_commit(p).committer_ident.ts, counter, p))
                counter += 1


def format_commit(sha: str, c: Commit, decoration: str = "") -> str:
    lines = [f"commit {sha}{decoration}"]
    if len(c.parents) > 1:
        lines.append("Merge: " + " ".join(p[:7] for p in c.parents))
    who = c.author_ident
    lines.append(f"Author: {who.who}")
    lines.append(f"Date:   {format_date(who.ts, who.tz)}")
    lines.append("")
    body = c.message.rstrip("\n")
    lines.extend("    " + ln for ln in body.split("\n")) if body else None
    return "\n".join(lines)


def _decorations(repo) -> dict[str, list[str]]:
    deco: dict[str, list[str]] = {}
    ref, head = repo.head_state()
    for name, sha in R.list_refs(repo.gitdir, "refs/").items():
        if name.startswith("refs/heads/"):
            label = name[11:]
            if ref == name:
                label = "HEAD -> " + label
            deco.setdefault(sha, []).insert(0 if ref == name else len(deco.get(sha, [])), label)
        elif name.startswith("refs/tags/"):
            try:
                target = repo.peel(sha, "commit")
            except GitError:
                continue
            deco.setdefault(target, []).append("tag: " + name[10:])
    if not ref and head:
        deco.setdefault(head, []).insert(0, "HEAD")
    return deco


def _graph_lines(commits):
    cols: list[str] = []
    for sha, c in commits:
        if sha not in cols:
            cols.append(sha)
        i = cols.index(sha)
        prefix = " ".join("*" if j == i else "|" for j in range(len(cols)))
        yield prefix, sha, c
        new: list[str] = []
        for j, s in enumerate(cols):
            if j == i:
                for p in c.parents:
                    if p not in cols and p not in new:
                        new.append(p)
                    elif p == c.parents[0] and p not in new:
                        new.append(p)
            elif s != sha:
                new.append(s)
        cols = new


def cmd_log(a) -> int:
    repo = Repo.discover()
    if a.revision:
        start = [repo.resolve_commit(r) for r in a.revision]
    else:
        head = repo.head_sha()
        if not head:
            raise GitError(f"your current branch '{repo.current_branch() or 'HEAD'}' does not have any commits yet")
        start = [head]
    deco = _decorations(repo) if (a.decorate or (sys.stdout.isatty() and not a.no_decorate)) else {}

    def deco_str(sha):
        return f" ({', '.join(deco[sha])})" if sha in deco else ""

    commits = iter_commits(repo, start)
    if a.max_count is not None:
        commits = (x for i, x in enumerate(commits) if i < a.max_count)
    if a.graph:
        for prefix, sha, c in _graph_lines(commits):
            out(f"{prefix} {sha[:7]}{deco_str(sha)} {c.subject}")
        return 0
    first = True
    for sha, c in commits:
        if a.oneline:
            out(f"{sha[:7]}{deco_str(sha)} {c.subject}")
        else:
            if not first:
                out()
            out(format_commit(sha, c, deco_str(sha)))
        first = False
    return 0


def cmd_show(a) -> int:
    repo = Repo.discover()
    sha = repo.resolve(a.object)
    t, data = repo.read_object(sha)
    while t == "tag":
        tag = Tag.parse(data)
        out(f"tag {tag.name}")
        if tag.tagger:
            who = parse_ident(tag.tagger)
            out(f"Tagger: {who.who}")
            out(f"Date:   {format_date(who.ts, who.tz)}")
        out()
        out(tag.message.rstrip("\n"))
        out()
        sha = tag.object
        t, data = repo.read_object(sha)
    if t == "commit":
        c = Commit.parse(data)
        out(format_commit(sha, c))
        if len(c.parents) <= 1:
            old = repo.flatten_tree(repo.read_commit(c.parents[0]).tree) if c.parents else {}
            new = repo.flatten_tree(c.tree)
            chunks = []
            for p in sorted(set(old) | set(new), key=pkey):
                if old.get(p) == new.get(p):
                    continue
                o, n = old.get(p), new.get(p)
                chunks.append(format_file_diff(p, (o[0], o[1], repo.read_object(o[1])[1]) if o else None,
                                               (n[0], n[1], repo.read_object(n[1])[1]) if n else None))
            if chunks:
                out()
                sys.stdout.write("".join(chunks))
    elif t == "tree":
        out(f"tree {a.object}\n")
        for e in parse_tree(data):
            out(q(e.name) + ("/" if e.mode == MODE_TREE else ""))
    else:
        write_bytes(data)
    return 0


def cmd_branch(a) -> int:
    repo = Repo.discover()
    gd = repo.gitdir
    if a.delete or a.force_delete:
        current = repo.current_branch()
        for name in a.names:
            ref = f"refs/heads/{name}"
            sha = R.read_ref(gd, ref)
            if sha is None:
                raise GitError(f"branch '{name}' not found.", 1)
            if name == current:
                raise GitError(f"cannot delete branch '{name}' checked out at '{repo.root}'", 1)
            head = repo.head_sha()
            if not a.force_delete and not (head and repo.is_ancestor(sha, head)):
                raise GitError(f"the branch '{name}' is not fully merged.\n"
                               f"If you are sure you want to delete it, run 'pygit branch -D {name}'.", 1)
            R.delete_ref(gd, ref)
            out(f"Deleted branch {name} (was {sha[:7]}).")
        return 0
    if a.names:
        name = a.names[0]
        ref = f"refs/heads/{name}"
        if not R.valid_ref_name(ref):
            raise GitError(f"'{name}' is not a valid branch name")
        if R.read_ref(gd, ref):
            raise GitError(f"a branch named '{name}' already exists")
        if len(a.names) > 1:
            start = repo.resolve_commit(a.names[1])
        else:
            start = repo.head_sha()
            if not start:
                raise GitError("cannot create a branch yet: the current branch has no commits (make a commit first)")
        R.write_ref(gd, ref, start)
        return 0
    branches = R.list_refs(gd, "refs/heads/")
    current = repo.current_branch()
    if repo.head_state()[0] is None and repo.head_sha():
        out(f"* (HEAD detached at {repo.head_sha()[:7]})")
    for ref in branches:
        name = ref[11:]
        line = f"{'*' if name == current else ' '} {name}"
        if a.verbose:
            c = repo.read_commit(branches[ref])
            line += f" {branches[ref][:7]} {c.subject}"
        out(line)
    return 0


def cmd_tag(a) -> int:
    repo = Repo.discover()
    gd = repo.gitdir
    if a.delete:
        for name in a.names:
            sha = R.read_ref(gd, f"refs/tags/{name}")
            if sha is None:
                raise GitError(f"tag '{name}' not found.", 1)
            R.delete_ref(gd, f"refs/tags/{name}")
            out(f"Deleted tag '{name}' (was {sha[:7]})")
        return 0
    if not a.names:
        for ref in R.list_refs(gd, "refs/tags/"):
            out(ref[10:])
        return 0
    name = a.names[0]
    ref = f"refs/tags/{name}"
    if not R.valid_ref_name(ref):
        raise GitError(f"'{name}' is not a valid tag name.")
    if R.read_ref(gd, ref):
        raise GitError(f"tag '{name}' already exists", 128)
    target = repo.resolve(a.names[1]) if len(a.names) > 1 else repo.head_sha()
    if not target:
        raise GitError("cannot tag yet: there are no commits")
    if a.annotate or a.message:
        if not a.message:
            raise GitError("annotated tags need a message (use -m \"message\")", 1)
        message = clean_message("\n\n".join(a.message))
        target = repo.write_tag(name, target, repo.identity("COMMITTER"), message)
    R.write_ref(gd, ref, target)
    return 0


def _switch(repo, name, create=False, start=None, detach=False, force=False, checkout_style=False) -> int:
    gd = repo.gitdir
    head_ref, head_sha = repo.head_state()
    ref = f"refs/heads/{name}"
    if create:
        if not R.valid_ref_name(ref):
            raise GitError(f"'{name}' is not a valid branch name")
        if R.read_ref(gd, ref):
            raise GitError(f"a branch named '{name}' already exists")
        if start is None and head_sha is None:
            R.write_symref(gd, "HEAD", ref)
            err(f"Switched to a new branch '{name}'")
            return 0
        target = repo.resolve_commit(start or "HEAD")
        checkout_tree(repo, head_sha, target, force)
        R.write_ref(gd, ref, target)
        R.write_symref(gd, "HEAD", ref)
        err(f"Switched to a new branch '{name}'")
        return 0
    branch_sha = R.read_ref(gd, ref) if R.valid_ref_name(ref) else None
    if branch_sha and not detach:
        if head_ref == ref:
            err(f"Already on '{name}'")
            return 0
        checkout_tree(repo, head_sha, branch_sha, force)
        R.write_symref(gd, "HEAD", ref)
        err(f"Switched to branch '{name}'")
        return 0
    if not checkout_style and not detach:
        raise GitError(f"invalid reference: {name}", 128)
    target = repo.resolve_commit(name)
    checkout_tree(repo, head_sha, target, force)
    R.write_detached_head(gd, target)
    err(f"HEAD is now at {target[:7]} {repo.read_commit(target).subject}")
    return 0


def cmd_switch(a) -> int:
    repo = Repo.discover()
    if a.create:
        return _switch(repo, a.create, create=True, start=a.branch, force=a.force)
    if not a.branch:
        raise GitError("missing branch name", 129)
    return _switch(repo, a.branch, detach=a.detach, force=a.force)


def cmd_checkout(a) -> int:
    repo = Repo.discover()
    if a.dashdash:                       # pygit checkout [--] <paths>: restore files from the index
        idx = repo.load_index()
        for arg in a.dashdash:
            rel = repo.rel(arg)
            matched = [p for p in idx.entries if p == rel or p.startswith(rel + "/")] if rel else list(idx.entries)
            if not matched:
                raise GitError(f"pathspec '{arg}' did not match any file(s) known to pygit", 1)
            for p in matched:
                e = idx.entries[p]
                from .worktree import write_worktree_file
                write_worktree_file(repo.root, p, e.mode, repo.read_object(e.sha)[1])
        return 0
    if a.new_branch:
        return _switch(repo, a.new_branch, create=True, start=a.target, force=a.force)
    if not a.target:
        raise GitError("missing branch or commit argument", 129)
    return _switch(repo, a.target, detach=a.detach, force=a.force, checkout_style=True)
