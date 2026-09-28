import os

import helpers


class AddTests(helpers.RepoCase):
    def setUp(self):
        super().setUp()
        self.init("pygit")

    def test_add_single_file(self):
        self.write("a.txt", "a\n")
        self.write("b.txt", "b\n")
        self.pg("add", "a.txt")
        self.assertEqual(self.git("ls-files"), "a.txt\n")

    def test_add_directory_recursively(self):
        self.write("d/1.txt", "1")
        self.write("d/e/2.txt", "2")
        self.write("other.txt", "o")
        self.pg("add", "d")
        self.assertEqual(self.git("ls-files"), "d/1.txt\nd/e/2.txt\n")

    def test_add_dot_and_git_dir_is_never_added(self):
        self.write("a", "a")
        self.write("d/b", "b")
        self.pg("add", ".")
        self.assertEqual(self.git("ls-files"), "a\nd/b\n")

    def test_add_respects_gitignore_when_walking(self):
        self.write(".gitignore", "*.log\nbuild/\n")
        self.write("a.txt", "a")
        self.write("x.log", "l")
        self.write("build/o", "o")
        self.pg("add", ".")
        self.assertEqual(self.git("ls-files"), ".gitignore\na.txt\n")

    def test_add_explicit_ignored_file_is_refused(self):
        self.write(".gitignore", "*.log\n")
        self.write("x.log", "l")
        err = self.pg_fail("add", "x.log", code=1)
        self.assertIn("ignored", err)
        self.assertEqual(self.git("ls-files"), "")

    def test_add_stages_deletions(self):
        self.write("a", "a")
        self.write("b", "b")
        self.pg("add", ".")
        os.remove(self.path("a"))
        self.pg("add", ".")
        self.assertEqual(self.git("ls-files"), "b\n")

    def test_add_a_deleted_path_by_name(self):
        self.write("a", "a")
        self.pg("add", "a")
        os.remove(self.path("a"))
        self.pg("add", "a")
        self.assertEqual(self.git("ls-files"), "")

    def test_add_updates_modified_file(self):
        self.write("a", "one\n")
        self.pg("add", "a")
        first = self.git("ls-files", "-s")
        self.write("a", "two\n")
        self.pg("add", "a")
        self.assertNotEqual(first, self.git("ls-files", "-s"))
        self.assertEqual(self.git("cat-file", "-p", ":a"), "two\n")

    def test_add_is_idempotent(self):
        self.write("a", "one\n")
        self.pg("add", "a")
        before = self.read(".git/index")
        self.pg("add", "a")
        self.assertEqual(self.git("ls-files", "-s"), self.git("ls-files", "-s"))
        self.assertEqual(len(before), len(self.read(".git/index")))

    def test_add_from_subdirectory_uses_cwd_relative_paths(self):
        self.write("sub/x.txt", "x")
        self.write("top.txt", "t")
        self.pg("add", "x.txt", cwd=self.path("sub"))
        self.pg("add", "../top.txt", cwd=self.path("sub"))
        self.assertEqual(self.git("ls-files"), "sub/x.txt\ntop.txt\n")

    def test_add_dot_in_subdirectory_only_adds_that_subtree(self):
        self.write("sub/x.txt", "x")
        self.write("top.txt", "t")
        self.pg("add", ".", cwd=self.path("sub"))
        self.assertEqual(self.git("ls-files"), "sub/x.txt\n")

    def test_add_unknown_path(self):
        self.assertIn("did not match any files", self.pg_fail("add", "ghost.txt"))

    def test_add_outside_repository(self):
        self.assertIn("outside repository", self.pg_fail("add", "../elsewhere"))

    def test_add_without_arguments(self):
        self.assertIn("Nothing specified", self.pg_fail("add", code=1))

    def test_add_file_replacing_directory_in_index(self):
        self.write("x/y", "y")
        self.pg("add", ".")
        os.remove(self.path("x/y"))
        os.rmdir(self.path("x"))
        self.write("x", "now a file")
        self.pg("add", ".")
        self.assertEqual(self.git("ls-files"), "x\n")

    def test_add_special_names(self):
        for name in ("中文.txt", "with space.txt", "quote'.txt", "-dash.txt", "tab\there.txt", "emoji-🙂.txt"):
            self.write(name, "x")
        self.pg("add", ".")
        self.assertEqual(sorted(self.gitb("ls-files", "-z").split(b"\0")[:-1]),
                         sorted(n.encode() for n in os.listdir(self.dir) if n != ".git"))


class RmTests(helpers.RepoCase):
    def setUp(self):
        super().setUp()
        self.init()
        self.write("a.txt", "a\n")
        self.write("d/b.txt", "b\n")
        self.write("d/e/c.txt", "c\n")
        self.git("add", "-A")
        self.git_commit("base")

    def test_rm_file(self):
        out = self.pg("rm", "a.txt")
        self.assertEqual(out, "rm 'a.txt'\n")
        self.assertFalse(self.exists("a.txt"))
        self.assertEqual(self.git("status", "--porcelain"), "D  a.txt\n")

    def test_rm_cached_keeps_file(self):
        self.pg("rm", "--cached", "a.txt")
        self.assertTrue(self.exists("a.txt"))
        self.assertEqual(self.git("status", "--porcelain"), "D  a.txt\n?? a.txt\n")

    def test_rm_directory_needs_r(self):
        self.assertIn("without -r", self.pg_fail("rm", "d"))
        self.pg("rm", "-r", "d")
        self.assertFalse(self.exists("d"))
        self.assertEqual(self.git("ls-files"), "a.txt\n")

    def test_rm_refuses_locally_modified_file(self):
        self.write("a.txt", "changed\n")
        self.assertIn("local modifications", self.pg_fail("rm", "a.txt", code=1))
        self.assertTrue(self.exists("a.txt"))
        self.pg("rm", "-f", "a.txt")
        self.assertFalse(self.exists("a.txt"))

    def test_rm_refuses_staged_changes(self):
        self.write("new.txt", "n\n")
        self.git("add", "new.txt")
        self.assertIn("staged in the index", self.pg_fail("rm", "new.txt", code=1))

    def test_rm_unknown(self):
        self.assertIn("did not match", self.pg_fail("rm", "nope"))

    def test_rm_removes_empty_directories(self):
        self.pg("rm", "d/e/c.txt")
        self.assertFalse(self.exists("d/e"))
        self.assertTrue(self.exists("d/b.txt"))


class CommitTests(helpers.RepoCase):
    def setUp(self):
        super().setUp()
        self.init("pygit")

    def test_first_commit_output_and_object(self):
        self.write("a.txt", "a\n")
        self.pg("add", "a.txt")
        self.tick()
        out = self.pg("commit", "-m", "first commit")
        self.assertRegex(out, r"^\[main \(root-commit\) [0-9a-f]{7}\] first commit\n$")
        sha = self.git("rev-parse", "HEAD").strip()
        self.assertTrue(out.startswith(f"[main (root-commit) {sha[:7]}]"))

    def test_commit_object_is_exactly_what_git_expects(self):
        self.write("a.txt", "a\n")
        self.pg("add", "a.txt")
        self.pg("commit", "-m", "hello")
        tree = self.git("rev-parse", "HEAD^{tree}").strip()
        self.assertEqual(self.git("cat-file", "commit", "HEAD"),
                         f"tree {tree}\nauthor Test Author <author@example.com> 1700000000 +0800\n"
                         "committer Test Committer <committer@example.com> 1700000000 +0800\n\nhello\n")
        self.fsck()

    def test_second_commit_has_parent(self):
        self.write("a.txt", "1\n")
        self.pg("add", "a.txt")
        self.pg_commit("one")
        self.write("a.txt", "2\n")
        self.pg("add", "a.txt")
        self.pg_commit("two")
        self.assertEqual(self.git("log", "--format=%s").split(), ["two", "one"])
        self.assertEqual(len(self.git("rev-list", "--parents", "-n1", "HEAD").split()), 2)

    def test_commit_message_is_cleaned(self):
        self.write("a", "a")
        self.pg("add", "a")
        self.pg("commit", "-m", "  subject   \n\n\n\nbody line  \n\n")
        self.assertEqual(self.git("cat-file", "commit", "HEAD").split("\n\n", 1)[1], "  subject\n\nbody line\n")

    def test_multiple_m_flags_become_paragraphs(self):
        self.write("a", "a")
        self.pg("add", "a")
        self.pg("commit", "-m", "subject", "-m", "details")
        self.assertEqual(self.git("log", "-1", "--format=%B").strip(), "subject\n\ndetails")

    def test_unicode_message(self):
        self.write("a", "a")
        self.pg("add", "a")
        self.pg("commit", "-m", "新增檔案 🙂")
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "新增檔案 🙂")
        self.fsck()

    def test_nothing_to_commit(self):
        self.write("a", "a")
        self.pg("add", "a")
        self.pg_commit("one")
        self.assertIn("nothing to commit", self.pg_fail("commit", "-m", "again", code=1))

    def test_nothing_to_commit_with_untracked(self):
        self.write("a", "a")
        self.assertIn("untracked files present", self.pg_fail("commit", "-m", "x", code=1))

    def test_allow_empty(self):
        self.write("a", "a")
        self.pg("add", "a")
        self.pg_commit("one")
        self.pg("commit", "--allow-empty", "-m", "empty")
        self.assertEqual(self.git("rev-list", "--count", "HEAD").strip(), "2")

    def test_commit_requires_message(self):
        self.write("a", "a")
        self.pg("add", "a")
        self.assertIn("empty commit message", self.pg_fail("commit", code=1))
        self.assertIn("empty commit message", self.pg_fail("commit", "-m", "   ", code=1))

    def test_commit_all_flag(self):
        self.write("a", "a\n")
        self.write("b", "b\n")
        self.pg("add", ".")
        self.pg_commit("base")
        self.write("a", "changed\n")
        os.remove(self.path("b"))
        self.write("untracked", "u")
        self.pg("commit", "-a", "-m", "all")
        self.assertEqual(self.git("status", "--porcelain"), "?? untracked\n")
        self.assertEqual(self.git("ls-files"), "a\n")

    def test_author_from_environment_and_timezone(self):
        self.write("a", "a")
        self.pg("add", "a")
        env = self.env(GIT_AUTHOR_NAME="Env Author", GIT_AUTHOR_EMAIL="env@example.org",
                       GIT_AUTHOR_DATE="2024-02-29T12:00:00-05:00", GIT_COMMITTER_DATE="@1700000000 -0730")
        self.run_cmd(["python3", "-m", "pygit", "commit", "-m", "tz"], env=env)
        raw = self.git("cat-file", "commit", "HEAD")
        self.assertIn("author Env Author <env@example.org> 1709226000 -0500", raw)
        self.assertIn("committer Test Committer <committer@example.com> 1700000000 -0730", raw)

    def test_author_from_repo_config(self):
        self.write("a", "a")
        self.pg("add", "a")
        self.pg("config", "user.name", "Config Person")
        self.pg("config", "user.email", "cfg@example.com")
        env = {k: v for k, v in self.env().items() if not k.startswith("GIT_AUTHOR") and not k.startswith("GIT_COMMITTER")}
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        self.run_cmd(["python3", "-m", "pygit", "commit", "-m", "cfg"], env=env)
        self.assertEqual(self.git("log", "-1", "--format=%an <%ae> | %cn <%ce>").strip(),
                         "Config Person <cfg@example.com> | Config Person <cfg@example.com>")

    def test_author_from_global_config_file(self):
        self.write("a", "a")
        self.pg("add", "a")
        gc = os.path.join(self.home, "gitconfig")
        with open(gc, "w") as f:
            f.write('[user]\n\tname = "Global Person"\n\temail = g@example.com ; comment\n')
        env = {k: v for k, v in self.env().items() if not k.startswith("GIT_AUTHOR") and not k.startswith("GIT_COMMITTER")}
        env["GIT_CONFIG_GLOBAL"] = gc
        self.run_cmd(["python3", "-m", "pygit", "commit", "-m", "g"], env=env)
        self.assertEqual(self.git("log", "-1", "--format=%an <%ae>").strip(), "Global Person <g@example.com>")

    def test_missing_identity_is_explained(self):
        self.write("a", "a")
        self.pg("add", "a")
        env = {k: v for k, v in self.env().items() if not k.startswith("GIT_AUTHOR") and not k.startswith("GIT_COMMITTER")}
        env.pop("EMAIL", None)
        p = self.run_cmd(["python3", "-m", "pygit", "commit", "-m", "x"], env=env, check=False)
        self.assertEqual(p.returncode, 128)
        self.assertIn(b"Author identity unknown", p.stderr)

    def test_commit_on_detached_head(self):
        self.write("a", "1\n")
        self.pg("add", "a")
        self.pg_commit("one")
        self.pg("checkout", "--detach", "HEAD")
        self.write("a", "2\n")
        self.pg("add", "a")
        self.tick()
        out = self.pg("commit", "-m", "detached")
        self.assertIn("[detached HEAD ", out)
        self.assertEqual(self.git("rev-parse", "HEAD").strip()[:7], out.split()[2].rstrip("]"))
        self.assertEqual(self.git("rev-parse", "main"), self.git("rev-parse", "HEAD~1"))

    def test_commit_from_subdirectory(self):
        self.write("sub/a", "a")
        self.pg("add", "sub/a")
        self.pg("commit", "-m", "from sub", cwd=self.path("sub"))
        self.assertEqual(self.git("log", "--format=%s").strip(), "from sub")


class BranchTagTests(helpers.RepoCase):
    def setUp(self):
        super().setUp()
        self.ids = self.make_history()

    def test_branch_list_marks_current(self):
        self.git("branch", "zeta")
        self.git("branch", "alpha")
        self.assertEqual(self.pg("branch"), "  alpha\n* main\n  zeta\n")

    def test_branch_create_and_git_sees_it(self):
        self.pg("branch", "topic")
        self.assertEqual(self.git("rev-parse", "topic").strip(), self.ids[2])
        self.pg("branch", "old", "HEAD~2")
        self.assertEqual(self.git("rev-parse", "old").strip(), self.ids[0])

    def test_branch_with_slash(self):
        self.pg("branch", "feature/login")
        self.assertIn("feature/login", self.pg("branch"))
        self.assertEqual(self.git("rev-parse", "feature/login").strip(), self.ids[2])

    def test_branch_verbose(self):
        self.assertEqual(self.pg("branch", "-v"), f"* main {self.ids[2][:7]} commit 2\n")

    def test_branch_already_exists(self):
        self.assertIn("already exists", self.pg_fail("branch", "main"))

    def test_branch_invalid_name(self):
        self.assertIn("not a valid branch name", self.pg_fail("branch", "bad..name"))

    def test_delete_merged_branch(self):
        self.git("branch", "merged")
        out = self.pg("branch", "-d", "merged")
        self.assertEqual(out, f"Deleted branch merged (was {self.ids[2][:7]}).\n")
        self.assertEqual(self.git("branch", "--list", "merged"), "")

    def test_delete_unmerged_branch_refused_until_forced(self):
        self.git("switch", "-q", "-c", "wip")
        self.write("wip.txt", "w")
        self.git("add", "wip.txt")
        self.git_commit("wip")
        self.git("switch", "-q", "main")
        self.assertIn("not fully merged", self.pg_fail("branch", "-d", "wip", code=1))
        self.pg("branch", "-D", "wip")
        self.assertEqual(self.git("branch", "--list", "wip"), "")

    def test_cannot_delete_current_branch(self):
        self.assertIn("cannot delete branch", self.pg_fail("branch", "-d", "main", code=1))

    def test_delete_missing_branch(self):
        self.assertIn("not found", self.pg_fail("branch", "-d", "ghost", code=1))

    def test_branch_in_repo_without_commits(self):
        d = self.dir + "-fresh"
        os.mkdir(d)
        self.run_cmd(["git", "init", "-q", "-b", "main"], cwd=d)
        self.assertIn("no commits", self.pg_fail("branch", "x", cwd=d))
        self.assertEqual(self.pg("branch", cwd=d), "")

    def test_detached_head_shown_in_branch_list(self):
        self.git("checkout", "-q", "--detach", "HEAD~1")
        self.assertTrue(self.pg("branch").startswith(f"* (HEAD detached at {self.ids[1][:7]})\n"))

    def test_lightweight_tag(self):
        self.pg("tag", "v1")
        self.assertEqual(self.git("cat-file", "-t", "v1").strip(), "commit")
        self.assertEqual(self.git("rev-parse", "v1").strip(), self.ids[2])

    def test_tag_on_specific_commit(self):
        self.pg("tag", "old", "HEAD~2")
        self.assertEqual(self.git("rev-parse", "old").strip(), self.ids[0])

    def test_annotated_tag(self):
        self.tick()
        self.pg("tag", "-a", "v2", "-m", "release two")
        self.assertEqual(self.git("cat-file", "-t", "v2").strip(), "tag")
        self.assertEqual(self.git("cat-file", "tag", "v2"),
                         f"object {self.ids[2]}\ntype commit\ntag v2\n"
                         f"tagger Test Committer <committer@example.com> {self.clock} +0800\n\nrelease two\n")
        self.assertEqual(self.git("rev-parse", "v2^{commit}").strip(), self.ids[2])
        self.fsck()

    def test_m_implies_annotated(self):
        self.pg("tag", "-m", "note", "v3")
        self.assertEqual(self.git("cat-file", "-t", "v3").strip(), "tag")

    def test_annotated_tag_needs_message(self):
        self.assertIn("message", self.pg_fail("tag", "-a", "v9", code=1))

    def test_tag_list_sorted(self):
        for t in ("b", "a", "c"):
            self.git("tag", t)
        self.assertEqual(self.pg("tag"), "a\nb\nc\n")
        self.assertEqual(self.pg("tag"), self.git("tag"))

    def test_tag_exists(self):
        self.pg("tag", "dup")
        self.assertIn("already exists", self.pg_fail("tag", "dup"))

    def test_delete_tag(self):
        self.git("tag", "gone")
        self.assertEqual(self.pg("tag", "-d", "gone"), f"Deleted tag 'gone' (was {self.ids[2][:7]})\n")
        self.assertEqual(self.git("tag"), "")

    def test_invalid_tag_name(self):
        self.assertIn("not a valid tag name", self.pg_fail("tag", "no good"))


class ConfigTests(helpers.RepoCase):
    def setUp(self):
        super().setUp()
        self.init("pygit")

    def test_set_and_get(self):
        self.pg("config", "user.name", "Alice Example")
        self.assertEqual(self.pg("config", "user.name"), "Alice Example\n")
        self.assertEqual(self.git("config", "user.name").strip(), "Alice Example")

    def test_overwrite_and_sections(self):
        self.pg("config", "user.name", "One")
        self.pg("config", "user.name", "Two")
        self.pg("config", "core.editor", "vim")
        self.pg("config", "branch.main.remote", "origin")
        self.assertEqual(self.git("config", "user.name").strip(), "Two")
        self.assertEqual(self.git("config", "core.editor").strip(), "vim")
        self.assertEqual(self.git("config", "branch.main.remote").strip(), "origin")
        self.assertEqual(self.git("config", "core.filemode").strip(), "true")

    def test_value_needing_quotes(self):
        self.pg("config", "user.name", 'Q "quoted" # name')
        self.assertEqual(self.git("config", "user.name").strip(), 'Q "quoted" # name')

    def test_missing_key_exits_1(self):
        p = self.run_cmd(["python3", "-m", "pygit", "config", "nope.nothing"], check=False)
        self.assertEqual(p.returncode, 1)

    def test_list(self):
        self.pg("config", "user.name", "L")
        self.assertIn("user.name=L", self.pg("config", "--list"))
