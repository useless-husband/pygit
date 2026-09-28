import os
import stat

import helpers


class CheckoutTests(helpers.RepoCase):
    def setUp(self):
        super().setUp()
        self.init()
        self.write("common.txt", "common\n")
        self.write("same.txt", "same everywhere\n")
        self.write("only-main.txt", "main only\n")
        self.write("shared/x.txt", "x main\n")
        self.write("run.sh", "#!/bin/sh\n", exec_=True)
        os.symlink("common.txt", self.path("link"))
        self.git("add", "-A")
        self.git_commit("main 1")
        self.git("branch", "feature")
        self.git("switch", "-q", "feature")
        os.remove(self.path("only-main.txt"))
        self.write("common.txt", "common on feature\n")
        self.write("feature-only.txt", "feature\n")
        self.write("deep/er/est/f.txt", "deep\n")
        self.write("shared/x.txt", "x feature\n")
        os.chmod(self.path("run.sh"), 0o644)
        os.remove(self.path("link"))
        os.symlink("feature-only.txt", self.path("link"))
        self.git("add", "-A")
        self.git_commit("feature 1")
        self.git("switch", "-q", "main")

    def clean(self):
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.pg("status", "--porcelain"), "")

    def test_switch_updates_worktree_and_index(self):
        self.pg("switch", "feature")
        self.assertEqual(self.git("symbolic-ref", "HEAD").strip(), "refs/heads/feature")
        self.assertEqual(self.read("common.txt"), b"common on feature\n")
        self.assertFalse(self.exists("only-main.txt"))
        self.assertEqual(self.read("deep/er/est/f.txt"), b"deep\n")
        self.clean()

    def test_switch_back_restores_everything(self):
        self.pg("switch", "feature")
        self.pg("switch", "main")
        self.assertEqual(self.read("only-main.txt"), b"main only\n")
        self.assertFalse(self.exists("deep"))          # emptied directories are removed
        self.assertFalse(self.exists("feature-only.txt"))
        self.clean()

    def test_modes_and_symlinks_are_restored(self):
        self.pg("switch", "feature")
        self.assertEqual(os.readlink(self.path("link")), "feature-only.txt")
        self.assertFalse(os.stat(self.path("run.sh")).st_mode & stat.S_IXUSR)
        self.pg("switch", "main")
        self.assertEqual(os.readlink(self.path("link")), "common.txt")
        self.assertTrue(os.stat(self.path("run.sh")).st_mode & stat.S_IXUSR)
        self.clean()

    def test_checkout_command_behaves_like_switch(self):
        self.pg("checkout", "feature")
        self.assertEqual(self.read("feature-only.txt"), b"feature\n")
        self.clean()

    def test_create_branch_with_checkout_b(self):
        self.pg("checkout", "-b", "topic")
        self.assertEqual(self.git("symbolic-ref", "HEAD").strip(), "refs/heads/topic")
        self.assertEqual(self.git("rev-parse", "topic"), self.git("rev-parse", "main"))

    def test_create_branch_with_switch_c_from_start_point(self):
        self.pg("switch", "-c", "from-feature", "feature")
        self.assertEqual(self.read("common.txt"), b"common on feature\n")
        self.assertEqual(self.git("rev-parse", "from-feature"), self.git("rev-parse", "feature"))
        self.clean()

    def test_already_on_branch(self):
        p = self.run_cmd(["python3", "-m", "pygit", "switch", "main"])
        self.assertIn("Already on 'main'", p.stderr.decode())

    def test_detached_checkout_of_commit(self):
        first = self.git("rev-parse", "feature").strip()
        self.pg("checkout", first)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), first)
        self.assertEqual(self.read("common.txt"), b"common on feature\n")
        self.assertIn("HEAD detached", self.pg("status"))
        self.clean()

    def test_detached_checkout_by_relative_rev(self):
        self.pg("switch", "feature")
        self.pg("checkout", "HEAD~1")
        self.assertEqual(self.read("only-main.txt"), b"main only\n")
        self.clean()

    def test_switch_refuses_unknown_branch(self):
        self.assertIn("invalid reference", self.pg_fail("switch", "nope"))

    def test_checkout_unknown_target(self):
        self.pg_fail("checkout", "nope")

    def test_refuses_when_local_change_would_be_overwritten(self):
        self.write("common.txt", "my precious edit\n")
        err = self.pg_fail("switch", "feature", code=1)
        self.assertIn("would be overwritten", err)
        self.assertIn("common.txt", err)
        self.assertEqual(self.read("common.txt"), b"my precious edit\n")
        self.assertEqual(self.git("symbolic-ref", "HEAD").strip(), "refs/heads/main")

    def test_refuses_when_staged_change_would_be_overwritten(self):
        self.write("common.txt", "staged edit\n")
        self.git("add", "common.txt")
        self.assertIn("would be overwritten", self.pg_fail("switch", "feature", code=1))
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "common.txt")

    def test_refuses_when_deleted_file_would_be_recreated_differently(self):
        os.remove(self.path("shared/x.txt"))
        self.assertIn("shared/x.txt", self.pg_fail("switch", "feature", code=1))

    def test_local_change_to_file_identical_on_both_branches_is_carried_over(self):
        self.write("same.txt", "local edit\n")
        self.pg("switch", "feature")
        self.assertEqual(self.read("same.txt"), b"local edit\n")
        self.assertIn(" M same.txt", self.pg("status", "--porcelain"))
        self.assertEqual(self.read("common.txt"), b"common on feature\n")

    def test_untracked_files_survive_switch(self):
        self.write("untracked-note.txt", "keep me\n")
        self.pg("switch", "feature")
        self.assertEqual(self.read("untracked-note.txt"), b"keep me\n")
        self.assertIn("?? untracked-note.txt", self.pg("status", "--porcelain"))

    def test_refuses_to_overwrite_untracked_file(self):
        self.write("feature-only.txt", "untracked precious\n")
        err = self.pg_fail("switch", "feature", code=1)
        self.assertIn("untracked working tree files would be overwritten", err)
        self.assertEqual(self.read("feature-only.txt"), b"untracked precious\n")

    def test_force_overwrites(self):
        self.write("common.txt", "throw away\n")
        self.pg("switch", "-f", "feature")
        self.assertEqual(self.read("common.txt"), b"common on feature\n")

    def test_file_and_directory_swap_between_branches(self):
        self.git("switch", "-q", "-c", "swap")
        os.remove(self.path("common.txt"))
        self.write("common.txt/inner.txt", "now a directory\n")
        os.remove(self.path("shared/x.txt"))
        os.rmdir(self.path("shared"))
        self.write("shared", "now a file\n")
        self.git("add", "-A")
        self.git_commit("swap")
        self.pg("switch", "main")
        self.assertEqual(self.read("common.txt"), b"common\n")
        self.assertTrue(os.path.isdir(self.path("shared")))
        self.clean()
        self.pg("switch", "swap")
        self.assertEqual(self.read("common.txt/inner.txt"), b"now a directory\n")
        self.assertEqual(self.read("shared"), b"now a file\n")
        self.clean()

    def test_git_sees_a_consistent_repository_after_many_switches(self):
        for _ in range(3):
            self.pg("switch", "feature")
            self.pg("switch", "main")
        self.clean()
        self.fsck()

    def test_commit_after_switch_extends_the_right_branch(self):
        self.pg("switch", "feature")
        self.write("more.txt", "m\n")
        self.pg("add", "more.txt")
        self.pg_commit("on feature")
        self.assertEqual(self.git("log", "--format=%s", "feature").split("\n")[:3], ["on feature", "feature 1", "main 1"])
        self.assertEqual(self.git("log", "--format=%s", "main").strip(), "main 1")

    def test_restore_file_from_index_with_checkout_dashdash(self):
        self.write("common.txt", "oops\n")
        self.pg("checkout", "--", "common.txt")
        self.assertEqual(self.read("common.txt"), b"common\n")
        os.remove(self.path("shared/x.txt"))
        self.pg("checkout", "--", "shared")
        self.assertEqual(self.read("shared/x.txt"), b"x main\n")
        self.clean()

    def test_switch_on_unborn_repository_creates_branch(self):
        d = self.dir + "-new"
        os.mkdir(d)
        self.run_cmd(["git", "init", "-q", "-b", "main"], cwd=d)
        self.pg("switch", "-c", "first", cwd=d)
        self.assertEqual(self.git("symbolic-ref", "HEAD", cwd=d).strip(), "refs/heads/first")

    def test_checkout_from_subdirectory(self):
        self.pg("switch", "feature", cwd=self.path("shared"))
        self.assertEqual(self.read("shared/x.txt"), b"x feature\n")
        self.clean()
