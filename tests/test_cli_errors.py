import os
import subprocess
import sys

import helpers


class OutsideRepositoryTests(helpers.RepoCase):
    COMMANDS = [["status"], ["log"], ["add", "."], ["commit", "-m", "x"], ["diff"], ["branch"], ["tag"],
                ["ls-files"], ["write-tree"], ["rev-parse", "HEAD"], ["cat-file", "-p", "HEAD"], ["show"],
                ["switch", "main"], ["checkout", "main"], ["rm", "x"], ["ls-tree", "HEAD"],
                ["update-ref", "refs/heads/x", "HEAD"], ["hash-object", "-w", "--stdin"]]

    def test_every_repository_command_explains_the_problem(self):
        for argv in self.COMMANDS:
            with self.subTest(argv=argv):
                p = self.run_cmd([sys.executable, "-m", "pygit", *argv], check=False, input=b"x")
                err = p.stderr.decode()
                self.assertEqual(p.returncode, 128, err)
                self.assertIn("fatal: not a git repository", err)
                self.assertNotIn("Traceback", err)

    def test_hash_object_without_w_works_anywhere(self):
        self.assertEqual(self.pg("hash-object", "--stdin", input=b"hello\n").strip(),
                         "ce013625030ba8dba906f756967f9e9ca394464a")

    def test_init_works_anywhere_and_git_directory_is_found_from_subdirectories(self):
        self.pg("init")
        os.makedirs(self.path("a/b/c"))
        self.assertEqual(self.pg("rev-parse", "--show-toplevel", cwd=self.path("a/b/c")).strip(), self.dir)


class MessageTests(helpers.RepoCase):
    def setUp(self):
        super().setUp()
        self.init("pygit")

    def test_unknown_command(self):
        p = self.run_cmd([sys.executable, "-m", "pygit", "frobnicate"], check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"invalid choice", p.stderr)

    def test_missing_file_for_hash_object(self):
        self.assertIn("could not open", self.pg_fail("hash-object", "nope.txt"))

    def test_hash_object_needs_input(self):
        self.pg_fail("hash-object", code=129)

    def test_cat_file_requires_exactly_one_mode(self):
        self.write("f", "x")
        sha = self.pg("hash-object", "-w", "f").strip()
        self.pg_fail("cat-file", "-p", "-t", sha, code=129)
        self.pg_fail("cat-file", sha, code=129)

    def test_corrupt_loose_object_gives_clean_error(self):
        self.write("f", "x")
        sha = self.pg("hash-object", "-w", "f").strip()
        p = self.path(f".git/objects/{sha[:2]}/{sha[2:]}")
        os.chmod(p, 0o644)
        with open(p, "wb") as f:
            f.write(b"garbage")
        self.assertIn("corrupt", self.pg_fail("cat-file", "-p", sha))

    def test_corrupt_index_gives_clean_error(self):
        self.write("f", "x")
        self.pg("add", "f")
        with open(self.path(".git/index"), "ab") as f:
            f.write(b"junk")
        self.assertIn("index file is corrupt", self.pg_fail("status"))

    def test_corrupt_head_ref(self):
        self.write("f", "x")
        self.pg("add", "f")
        self.pg_commit("c")
        with open(self.path(".git/refs/heads/main"), "w") as f:
            f.write("not a sha\n")
        self.assertIn("corrupt", self.pg_fail("log"))

    def test_invalid_date_in_environment(self):
        self.write("f", "x")
        self.pg("add", "f")
        p = self.run_cmd([sys.executable, "-m", "pygit", "commit", "-m", "x"], check=False,
                         env=self.env(GIT_AUTHOR_DATE="next tuesday"))
        self.assertEqual(p.returncode, 128)
        self.assertIn(b"invalid date format", p.stderr)

    def test_stale_index_lock_is_reported(self):
        self.write("f", "x")
        open(self.path(".git/index.lock"), "w").close()
        p = self.run_cmd([sys.executable, "-m", "pygit", "add", "f"], check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn(b"Traceback", p.stderr)
        self.assertIn(b"index.lock", p.stderr)

    def test_git_dir_file_worktree_layout(self):
        # `.git` may be a file pointing at the real directory (as in `git worktree` / submodules)
        real = os.path.join(self.tmp, "realgit")
        os.rename(self.path(".git"), real)
        with open(self.path(".git"), "w") as f:
            f.write(f"gitdir: {real}\n")
        self.write("f", "x")
        self.pg("add", "f")
        self.assertEqual(self.pg("ls-files"), "f\n")

    def test_stdout_closed_early_is_quiet(self):
        for i in range(30):
            self.write("f", f"{i}\n")
            self.pg("add", "f")
            self.pg_commit(f"c{i}")
        cmd = f"{sys.executable} -m pygit log | head -n 1"
        p = subprocess.run(cmd, shell=True, cwd=self.dir, env=self.env(), capture_output=True)
        self.assertNotIn(b"Traceback", p.stderr)


class BasicCliTests(helpers.RepoCase):
    def test_version(self):
        self.assertRegex(self.pg("--version"), r"^pygit \d+\.\d+\.\d+\n$")

    def test_no_arguments_prints_help(self):
        out = self.pg()
        self.assertIn("usage: pygit", out)
        for name in ("init", "add", "commit", "status", "log", "diff", "branch", "checkout", "switch", "tag", "show"):
            self.assertIn(name, out)

    def test_dash_C_changes_directory(self):
        self.init("pygit")
        self.write("f", "x")
        out = self.run_cmd([sys.executable, "-m", "pygit", "-C", self.dir, "status", "--porcelain"], cwd=self.tmp).stdout
        self.assertEqual(out, b"?? f\n")

    def test_help_for_subcommand(self):
        self.assertIn("usage: pygit commit", self.pg("commit", "--help"))

    def test_console_script_module_entry_point(self):
        from pygit.cli import main
        self.assertTrue(callable(main))
