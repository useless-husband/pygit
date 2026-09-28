"""status and diff must agree with real git in many situations.

Every scenario runs twice: once with the index written by git and once by pygit.
"""
import os

import helpers


class Scenarios:
    def setUp(self):
        super().setUp()
        self.init()

    def base(self):
        self.write("a.txt", "one\ntwo\nthree\n")
        self.write("sub/b.txt", "bee\n")
        self.write("sub/deep/c.txt", "sea\n")
        self.stage("a.txt", "sub")
        self.commit("base")

    def test_clean(self):
        self.base()
        self.assert_same_state()
        self.assertEqual(self.pg("status", "--porcelain"), "")

    def test_unborn_repository_with_staged_file(self):
        self.write("a.txt", "x\n")
        self.stage("a.txt")
        self.write("b.txt", "y\n")
        self.assert_same_state()

    def test_unborn_repository_untracked_only(self):
        self.write("a.txt", "x\n")
        self.assert_same_state()

    def test_modified_unstaged(self):
        self.base()
        self.write("a.txt", "one\nTWO\nthree\nfour\n")
        self.assert_same_state()

    def test_modified_staged(self):
        self.base()
        self.write("a.txt", "one\nchanged\n")
        self.stage("a.txt")
        self.assert_same_state()

    def test_staged_and_further_modified(self):
        self.base()
        self.write("a.txt", "first edit\n")
        self.stage("a.txt")
        self.write("a.txt", "second edit\n")
        self.assert_same_state()

    def test_new_file_staged(self):
        self.base()
        self.write("new.txt", "n\n")
        self.stage("new.txt")
        self.assert_same_state()

    def test_new_file_staged_then_deleted(self):
        self.base()
        self.write("new.txt", "n\n")
        self.stage("new.txt")
        os.remove(self.path("new.txt"))
        self.assert_same_state()

    def test_deleted_unstaged(self):
        self.base()
        os.remove(self.path("sub/b.txt"))
        self.assert_same_state()

    def test_deleted_staged(self):
        self.base()
        os.remove(self.path("sub/b.txt"))
        self.git("rm", "-q", "--cached", "sub/b.txt")
        self.assert_same_state()

    def test_untracked_file_and_directory_collapsing(self):
        self.base()
        self.write("u.txt", "u\n")
        self.write("newdir/x.txt", "x\n")
        self.write("newdir/y/z.txt", "z\n")
        self.write("sub/inside.txt", "i\n")
        self.write("sub/deep/more/m.txt", "m\n")
        self.assert_same_state()

    def test_ignored_files_are_hidden(self):
        self.write(".gitignore", "*.log\nbuild/\n!keep.log\n/only-root\n")
        self.write("a.txt", "a\n")
        self.stage(".gitignore", "a.txt")
        self.commit("base")
        self.write("x.log", "l\n")
        self.write("keep.log", "k\n")
        self.write("build/out.o", "o\n")
        self.write("sub/only-root", "r\n")
        self.write("only-root", "r\n")
        self.write("newdir/ignored.log", "l\n")
        self.write("onlyignored/deep/a.log", "l\n")
        self.assert_same_state()
        self.assertNotIn("x.log", self.pg("status", "--porcelain"))
        self.assertIn("keep.log", self.pg("status", "--porcelain"))

    def test_tracked_file_matching_ignore_rule_stays_tracked(self):
        self.write("a.log", "1\n")
        self.stage("-f", "a.log") if self.STAGER == "git" else None
        if self.STAGER == "pygit":
            self.git("add", "-f", "a.log")
        self.write(".gitignore", "*.log\n")
        self.stage(".gitignore")
        self.commit("base")
        self.write("a.log", "2\n")
        self.assert_same_state()
        self.assertIn(" M a.log", self.pg("status", "--porcelain"))

    def test_changes_in_subdirectories(self):
        self.base()
        self.write("sub/b.txt", "bee!\n")
        self.write("sub/deep/c.txt", "sea!\n")
        self.write("sub/deep/d.txt", "new\n")
        self.stage("sub/deep")
        self.write("sub/deep/d.txt", "new again\n")
        self.assert_same_state()

    def test_status_from_subdirectory_uses_root_relative_paths(self):
        self.base()
        self.write("sub/b.txt", "changed\n")
        self.write("sub/untracked.txt", "u\n")
        g = self.gitb("status", "--porcelain", cwd=self.path("sub"))
        p = self.pgb("status", "--porcelain", cwd=self.path("sub"))
        self.assertEqual(g, p)

    def test_chinese_file_names(self):
        self.write("中文.txt", "你好\n")
        self.write("資料夾/檔案.md", "內容\n")
        self.stage("中文.txt", "資料夾")
        self.commit("base")
        self.write("中文.txt", "你好，世界\n")
        self.write("資料夾/新的.md", "新\n")
        self.write("未追蹤.txt", "x\n")
        self.assert_same_state()

    def test_file_names_with_spaces(self):
        self.write("with space.txt", "a\n")
        self.write("dir with space/in side.txt", "b\n")
        self.stage("with space.txt", "dir with space")
        self.commit("base")
        self.write("with space.txt", "a2\n")
        self.write("dir with space/in side.txt", "b2\n")
        self.write("new file.txt", "n\n")
        self.assert_same_state()

    def test_empty_files(self):
        self.write("empty.txt", "")
        self.stage("empty.txt")
        self.commit("base")
        self.write("empty2.txt", "")
        self.stage("empty2.txt")
        self.assert_same_state()
        self.write("empty.txt", "now has text\n")
        self.assert_same_state()

    def test_executable_bit_change(self):
        self.write("run.sh", "#!/bin/sh\necho hi\n")
        self.stage("run.sh")
        self.commit("base")
        os.chmod(self.path("run.sh"), 0o755)
        self.assert_same_state()
        self.assertIn("old mode 100644\nnew mode 100755", self.pg("diff"))
        self.stage("run.sh")
        self.assert_same_state()

    def test_new_executable_file(self):
        self.base()
        self.write("tool.sh", "#!/bin/sh\n", exec_=True)
        self.stage("tool.sh")
        self.assert_same_state()
        self.assertIn("new file mode 100755", self.pg("diff", "--cached"))

    def test_executable_bit_and_content_change_together(self):
        self.write("run.sh", "one\n")
        self.stage("run.sh")
        self.commit("base")
        self.write("run.sh", "two\n", exec_=True)
        self.assert_same_state()

    def test_symlink_added_and_changed(self):
        self.base()
        os.symlink("a.txt", self.path("link"))
        self.assert_same_state()
        self.stage("link")
        self.assert_same_state()
        self.commit("link")
        os.remove(self.path("link"))
        os.symlink("sub/b.txt", self.path("link"))
        self.assert_same_state()
        self.assertIn("120000", self.pg("ls-files", "-s"))

    def test_dangling_symlink(self):
        self.base()
        os.symlink("does-not-exist", self.path("dangling"))
        self.stage("dangling")
        self.assert_same_state()

    def test_file_replaced_by_symlink(self):
        self.base()
        os.remove(self.path("a.txt"))
        os.symlink("sub/b.txt", self.path("a.txt"))
        self.assert_same_state()

    def test_symlink_to_directory_is_a_file_not_a_directory(self):
        self.base()
        os.symlink("sub", self.path("dirlink"))
        self.assert_same_state()
        self.stage("dirlink")
        self.assert_same_state()

    def test_binary_file_diff(self):
        self.write("img.bin", b"\x00\x01\x02binary")
        self.stage("img.bin")
        self.commit("base")
        self.write("img.bin", b"\x00\x01\x02changed")
        self.assert_same_state()
        self.assertIn("Binary files a/img.bin and b/img.bin differ", self.pg("diff"))

    def test_missing_newline_at_end_of_file(self):
        self.write("n.txt", "alpha\nbeta")
        self.stage("n.txt")
        self.commit("base")
        self.write("n.txt", "alpha\nbeta\n")
        self.assert_same_state()
        self.write("n.txt", "alpha\ngamma")
        self.assert_same_state()

    def test_crlf_content(self):
        self.write("w.txt", "a\r\nb\r\nc\r\n")
        self.stage("w.txt")
        self.commit("base")
        self.write("w.txt", "a\r\nB\r\nc\r\n")
        self.assert_same_state()

    def test_multiple_hunks_and_function_context(self):
        body = ["def f%d():" % i if i % 10 == 0 else "    line %d" % i for i in range(1, 80)]
        self.write("code.py", "\n".join(body) + "\n")
        self.stage("code.py")
        self.commit("base")
        changed = list(body)
        changed[5] = "    CHANGED 6"
        changed[30] = "    CHANGED 31"
        changed[31] = "    CHANGED 32"
        del changed[60]
        changed.insert(70, "    inserted")
        self.write("code.py", "\n".join(changed) + "\n")
        self.assert_same_state()
        self.assertGreaterEqual(self.pg("diff").count("@@ -"), 3)

    def test_whole_file_rewritten(self):
        self.write("r.txt", "".join(f"old {i}\n" for i in range(20)))
        self.stage("r.txt")
        self.commit("base")
        self.write("r.txt", "".join(f"new {i}\n" for i in range(25)))
        self.assert_same_state()

    def test_lines_added_at_start_and_end(self):
        self.write("s.txt", "".join(f"m{i}\n" for i in range(10)))
        self.stage("s.txt")
        self.commit("base")
        self.write("s.txt", "top\n" + "".join(f"m{i}\n" for i in range(10)) + "bottom\n")
        self.assert_same_state()

    def test_file_emptied(self):
        self.base()
        self.write("a.txt", "")
        self.assert_same_state()

    def test_diff_with_path_filter(self):
        self.base()
        self.write("a.txt", "A\n")
        self.write("sub/b.txt", "B\n")
        self.assertEqual(self.gitb("diff", "sub"), self.pgb("diff", "sub"))
        self.assertEqual(self.gitb("diff", "--", "a.txt"), self.pgb("diff", "--", "a.txt"))

    def test_directory_replaced_by_file(self):
        self.base()
        for p in ("sub/b.txt", "sub/deep/c.txt"):
            os.remove(self.path(p))
        os.rmdir(self.path("sub/deep"))
        os.rmdir(self.path("sub"))
        self.write("sub", "now a file\n")
        self.assert_same_state()

    def test_touched_file_with_same_content_is_clean(self):
        self.base()
        os.utime(self.path("a.txt"), (1_000_000_000, 1_000_000_000))
        self.assert_same_state()
        self.assertEqual(self.pg("status", "--porcelain"), "")

    def test_same_size_edit_within_same_second_is_detected(self):
        self.base()
        self.write("a.txt", "ONE\ntwo\nthree\n")      # same size as before
        self.assert_same_state()

    def test_nested_empty_directories_are_not_reported(self):
        self.base()
        os.makedirs(self.path("empty/dirs/inside"))
        self.assert_same_state()

    def test_long_status_mentions_all_sections(self):
        self.base()
        self.write("a.txt", "changed\n")
        self.write("staged.txt", "s\n")
        self.stage("staged.txt")
        self.write("untracked.txt", "u\n")
        text = self.pg("status")
        for expected in ("On branch main", "Changes to be committed:", "new file:   staged.txt",
                         "Changes not staged for commit:", "modified:   a.txt", "Untracked files:", "untracked.txt"):
            self.assertIn(expected, text)

    def test_long_status_clean(self):
        self.base()
        self.assertIn("nothing to commit, working tree clean", self.pg("status"))

    def test_long_status_no_commits_yet(self):
        self.assertIn("No commits yet", self.pg("status"))

    def test_short_status_branch_header(self):
        self.base()
        self.assertEqual(self.pg("status", "-s"), "## main\n")


class StatusDiffWithGitIndex(Scenarios, helpers.RepoCase):
    STAGER = "git"


class StatusDiffWithPygitIndex(Scenarios, helpers.RepoCase):
    STAGER = "pygit"
