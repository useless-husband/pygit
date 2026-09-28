"""Command line entry point (``pygit``)."""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__, commands as C
from .util import GitError


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pygit", description="A Git implementation written in pure Python.")
    p.add_argument("--version", action="version", version=f"pygit {__version__}")
    p.add_argument("-C", dest="chdir", metavar="DIR", help="run as if started in DIR")
    sub = p.add_subparsers(dest="cmd", metavar="<command>")

    def add(name, func, help):
        sp = sub.add_parser(name, help=help, description=help)
        sp.set_defaults(func=func)
        return sp

    s = add("init", C.cmd_init, "create an empty repository")
    s.add_argument("directory", nargs="?")
    s.add_argument("-b", "--initial-branch")

    s = add("hash-object", C.cmd_hash_object, "compute an object id, optionally store it")
    s.add_argument("-w", dest="write", action="store_true", help="write the object into the database")
    s.add_argument("-t", dest="type", default="blob", help="object type (default: blob)")
    s.add_argument("--stdin", action="store_true")
    s.add_argument("files", nargs="*")

    s = add("cat-file", C.cmd_cat_file, "show an object's type, size or content")
    s.add_argument("-t", dest="type", action="store_true")
    s.add_argument("-s", dest="size", action="store_true")
    s.add_argument("-p", dest="pretty", action="store_true")
    s.add_argument("-e", dest="exists", action="store_true")
    s.add_argument("objects", nargs="+", metavar="object")

    add("write-tree", C.cmd_write_tree, "write a tree object from the index")

    s = add("commit-tree", C.cmd_commit_tree, "create a commit object from a tree")
    s.add_argument("tree")
    s.add_argument("-p", dest="parent", action="append", default=[])
    s.add_argument("-m", dest="message", action="append", default=[])

    s = add("update-ref", C.cmd_update_ref, "create, update or delete a ref")
    s.add_argument("-d", dest="delete", action="store_true")
    s.add_argument("ref")
    s.add_argument("value", nargs="?")

    s = add("symbolic-ref", C.cmd_symbolic_ref, "read or set a symbolic ref such as HEAD")
    s.add_argument("name")
    s.add_argument("target", nargs="?")

    s = add("rev-parse", C.cmd_rev_parse, "resolve revision names to object ids")
    s.add_argument("--short", action="store_true", help="abbreviate ids to 7 characters")
    s.add_argument("--git-dir", action="store_true")
    s.add_argument("--show-toplevel", action="store_true")
    s.add_argument("revs", nargs="*")

    s = add("ls-files", C.cmd_ls_files, "list files in the index")
    s.add_argument("-s", "--stage", action="store_true")
    s.add_argument("-z", action="store_true")

    s = add("ls-tree", C.cmd_ls_tree, "list the contents of a tree")
    s.add_argument("-r", dest="recursive", action="store_true")
    s.add_argument("--name-only", action="store_true")
    s.add_argument("-z", action="store_true")
    s.add_argument("tree")

    s = add("config", C.cmd_config, "get or set configuration")
    s.add_argument("--global", dest="use_global", action="store_true")
    s.add_argument("--list", "-l", action="store_true")
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="?")

    s = add("add", C.cmd_add, "add files to the index")
    s.add_argument("paths", nargs="*")

    s = add("rm", C.cmd_rm, "remove files from the index and the work tree")
    s.add_argument("--cached", action="store_true")
    s.add_argument("-r", action="store_true")
    s.add_argument("-f", "--force", action="store_true")
    s.add_argument("paths", nargs="*")

    s = add("commit", C.cmd_commit, "record staged changes")
    s.add_argument("-m", dest="message", action="append", default=[])
    s.add_argument("-a", "--all", action="store_true")
    s.add_argument("--allow-empty", action="store_true")

    s = add("status", C.cmd_status, "show the working tree status")
    s.add_argument("-s", "--short", action="store_true")
    s.add_argument("--porcelain", action="store_true")
    s.add_argument("-z", action="store_true")

    s = add("log", C.cmd_log, "show commit history")
    s.add_argument("--oneline", action="store_true")
    s.add_argument("--graph", action="store_true")
    s.add_argument("--decorate", action="store_true")
    s.add_argument("--no-decorate", action="store_true")
    s.add_argument("-n", "--max-count", type=int)
    s.add_argument("revision", nargs="*")

    s = add("diff", C.cmd_diff, "show changes (work tree vs index, or --cached: index vs HEAD)")
    s.add_argument("--cached", "--staged", action="store_true")
    s.add_argument("paths", nargs="*")

    s = add("show", C.cmd_show, "show a commit, tag, tree or blob")
    s.add_argument("object", nargs="?", default="HEAD")

    s = add("branch", C.cmd_branch, "list, create or delete branches")
    s.add_argument("-d", "--delete", action="store_true")
    s.add_argument("-D", dest="force_delete", action="store_true")
    s.add_argument("-v", "--verbose", action="store_true")
    s.add_argument("names", nargs="*")

    s = add("tag", C.cmd_tag, "list, create or delete tags")
    s.add_argument("-a", dest="annotate", action="store_true")
    s.add_argument("-m", dest="message", action="append", default=[])
    s.add_argument("-d", dest="delete", action="store_true")
    s.add_argument("names", nargs="*")

    s = add("switch", C.cmd_switch, "switch branches")
    s.add_argument("-c", dest="create", metavar="NEW_BRANCH")
    s.add_argument("--detach", action="store_true")
    s.add_argument("-f", "--force", action="store_true")
    s.add_argument("branch", nargs="?")

    s = add("checkout", C.cmd_checkout, "switch branches or restore files from the index (after --)")
    s.add_argument("-b", dest="new_branch", metavar="NEW_BRANCH")
    s.add_argument("--detach", action="store_true")
    s.add_argument("-f", "--force", action="store_true")
    s.add_argument("target", nargs="?")
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    tail: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, tail = argv[:i], argv[i + 1:]
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="surrogateescape")
    parser = build_parser()
    args = parser.parse_args(argv)
    args.dashdash = tail
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        if args.chdir:
            os.chdir(args.chdir)
        return args.func(args) or 0
    except GitError as e:
        sys.stdout.flush()
        prefix = "fatal: " if e.code == 128 else "error: " if e.code == 1 else ""
        sys.stderr.write(f"{prefix}{e.message}\n")
        return e.code
    except OSError as e:
        if isinstance(e, BrokenPipeError):      # e.g. `pygit log | head`
            try:
                os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            except (OSError, ValueError):
                pass
            return 0
        sys.stdout.flush()
        sys.stderr.write(f"fatal: {e.strerror or e}" + (f": {e.filename}" if e.filename else "") + "\n")
        return 128
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
