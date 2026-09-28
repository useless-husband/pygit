"""Shared test helpers. Tests run real ``git`` and ``python -m pygit`` in temporary directories."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

BASE_TIME = 1700000000


class RepoCase(unittest.TestCase):
    """A fresh temp directory per test, with deterministic author, committer and dates."""

    STAGER = "git"      # which tool performs add/commit in helpers: "git" or "pygit"

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="pygit-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dir = os.path.join(self.tmp, "work")
        self.home = os.path.join(self.tmp, "home")
        os.mkdir(self.dir)
        os.mkdir(self.home)
        self.clock = BASE_TIME

    # ---- process helpers
    def env(self, **extra):
        e = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        stamp = f"{self.clock} +0800"
        e.update(
            HOME=self.home, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
            PYTHONPATH=SRC, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
            GIT_AUTHOR_NAME="Test Author", GIT_AUTHOR_EMAIL="author@example.com",
            GIT_COMMITTER_NAME="Test Committer", GIT_COMMITTER_EMAIL="committer@example.com",
            GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp, TZ="UTC")
        e.update(extra)
        return e

    def tick(self):
        self.clock += 60

    def run_cmd(self, argv, cwd=None, input=None, check=True, env=None):
        p = subprocess.run(argv, cwd=cwd or self.dir, env=env or self.env(), input=input,
                           capture_output=True)
        if check and p.returncode != 0:
            self.fail(f"command failed ({p.returncode}): {argv}\nstdout: {p.stdout!r}\nstderr: {p.stderr!r}")
        return p

    def gitb(self, *args, **kw):
        return self.run_cmd(["git", *args], **kw).stdout

    def pgb(self, *args, **kw):
        return self.run_cmd([sys.executable, "-m", "pygit", *args], **kw).stdout

    def git(self, *args, **kw):
        return self.gitb(*args, **kw).decode("utf-8", "surrogateescape")

    def pg(self, *args, **kw):
        return self.pgb(*args, **kw).decode("utf-8", "surrogateescape")

    def pg_fail(self, *args, code=None, **kw):
        """Run pygit expecting failure; returns stderr text. Never a Python traceback."""
        p = self.run_cmd([sys.executable, "-m", "pygit", *args], check=False, **kw)
        err = p.stderr.decode("utf-8", "replace")
        self.assertNotEqual(p.returncode, 0, f"expected failure: {args}")
        self.assertNotIn("Traceback", err)
        if code is not None:
            self.assertEqual(p.returncode, code, err)
        return err

    # ---- repo helpers
    def init(self, tool="git"):
        if tool == "git":
            self.git("init", "-q", "-b", "main")
        else:
            self.pg("init", "-b", "main")

    def path(self, rel):
        return os.path.join(self.dir, *rel.split("/"))

    def write(self, rel, content="", exec_=False):
        p = self.path(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(content if isinstance(content, bytes) else content.encode("utf-8"))
        if exec_:
            os.chmod(p, 0o755)

    def read(self, rel):
        with open(self.path(rel), "rb") as f:
            return f.read()

    def exists(self, rel):
        return os.path.lexists(self.path(rel))

    def stage(self, *paths):
        (self.git if self.STAGER == "git" else self.pg)("add", *paths)

    def commit(self, msg="commit", *extra):
        if self.STAGER == "git":
            self.git_commit(msg, *extra)
        else:
            self.pg_commit(msg, *extra)

    def git_commit(self, msg, *extra):
        self.tick()
        self.git("commit", "-q", "-m", msg, *extra)

    def pg_commit(self, msg, *extra):
        self.tick()
        self.pg("commit", "-m", msg, *extra)

    def fsck(self):
        p = self.run_cmd(["git", "fsck", "--strict", "--no-dangling"], check=False)
        self.assertEqual(p.returncode, 0, p.stderr.decode() + p.stdout.decode())
        self.assertNotIn(b"error", p.stderr.lower())
        self.assertNotIn(b"warning", p.stderr.lower())

    def make_history(self):
        """Three commits on main with a couple of files. Returns the list of commit ids."""
        self.init()
        ids = []
        for i in range(3):
            self.write("a.txt", f"line {i}\n" * (i + 1))
            self.write(f"dir/f{i}.txt", f"file {i}\n")
            self.git("add", "-A")
            self.git_commit(f"commit {i}")
            ids.append(self.git("rev-parse", "HEAD").strip())
        return ids

    def assert_same_state(self):
        """pygit and git agree about status (short and -z) and both kinds of diff."""
        gs = ["-c", "status.renames=false", "status", "--porcelain", "--untracked-files=normal"]
        self.assertEqual(self.gitb(*gs, "-z"), self.pgb("status", "--porcelain", "-z"))
        self.assertEqual(self.gitb(*gs), self.pgb("status", "--porcelain"))
        self.assertEqual(self.gitb("-c", "diff.renames=false", "diff"), self.pgb("diff"))
        self.assertEqual(self.gitb("-c", "diff.renames=false", "diff", "--cached"), self.pgb("diff", "--cached"))
