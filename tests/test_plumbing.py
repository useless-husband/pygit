import os

import helpers


class InitTests(helpers.RepoCase):
    def test_init_creates_repository_git_understands(self):
        out = self.pg("init")
        self.assertTrue(out.startswith("Initialized empty Git repository in "))
        for rel in ("HEAD", "config", "objects", "refs/heads", "refs/tags"):
            self.assertTrue(os.path.exists(self.path(".git/" + rel)), rel)
        self.assertEqual(self.git("rev-parse", "--is-inside-work-tree").strip(), "true")
        self.assertEqual(self.git("symbolic-ref", "HEAD").strip(), "refs/heads/main")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.fsck()

    def test_init_in_new_directory(self):
        self.pg("init", "sub/project")
        self.assertTrue(os.path.isdir(self.path("sub/project/.git")))

    def test_init_custom_branch(self):
        self.pg("init", "-b", "trunk")
        self.assertEqual(self.git("symbolic-ref", "HEAD").strip(), "refs/heads/trunk")

    def test_reinit_keeps_existing_data(self):
        self.pg("init")
        self.write("f", "x")
        self.pg("add", "f")
        self.assertTrue(self.pg("init").startswith("Reinitialized existing Git repository"))
        self.assertEqual(self.pg("ls-files").strip(), "f")

    def test_init_rejects_bad_branch_name(self):
        self.assertIn("invalid initial branch name", self.pg_fail("init", "-b", "bad name"))


class CatFileTests(helpers.RepoCase):
    def setUp(self):
        super().setUp()
        self.ids = self.make_history()

    def test_type_size_pretty_for_every_object_kind(self):
        self.git("tag", "-a", "-m", "annotated tag", "t1")
        specs = ["HEAD", "HEAD^{tree}", "HEAD:a.txt", "t1", "HEAD:dir"]
        for spec in specs:
            for flag in ("-t", "-s", "-p"):
                with self.subTest(spec=spec, flag=flag):
                    self.assertEqual(self.pgb("cat-file", flag, spec), self.gitb("cat-file", flag, spec))

    def test_exists_flag(self):
        self.pg("cat-file", "-e", "HEAD")
        p = self.run_cmd(["python3", "-m", "pygit", "cat-file", "-e", "0" * 40], check=False)
        self.assertEqual(p.returncode, 1)

    def test_type_and_object_form(self):
        self.assertEqual(self.pgb("cat-file", "blob", "HEAD:a.txt"), self.gitb("cat-file", "blob", "HEAD:a.txt"))
        self.assertEqual(self.pgb("cat-file", "commit", "HEAD"), self.gitb("cat-file", "commit", "HEAD"))
        self.assertIn("not a blob", self.pg_fail("cat-file", "blob", "HEAD"))

    def test_unknown_object(self):
        self.assertIn("Not a valid object name", self.pg_fail("cat-file", "-p", "deadbeef" * 5))

    def test_binary_content_is_written_verbatim(self):
        data = bytes(range(256))
        self.write("bin", data)
        sha = self.pg("hash-object", "-w", "bin").strip()
        self.assertEqual(self.pgb("cat-file", "-p", sha), data)

    def test_pretty_print_tree_with_unicode_name(self):
        self.write("中文.txt", "x")
        self.git("add", "-A")
        self.git_commit("unicode")
        self.assertEqual(self.pgb("cat-file", "-p", "HEAD^{tree}"), self.gitb("cat-file", "-p", "HEAD^{tree}"))


class TreeAndCommitTests(helpers.RepoCase):
    def test_write_tree_matches_git(self):
        self.init()
        self.write("a.txt", "a\n")
        self.write("d/b.txt", "b\n")
        self.write("d/e/c.sh", "#!/bin/sh\n", exec_=True)
        self.write("中文.txt", "x\n")
        self.git("add", "-A")
        self.assertEqual(self.pg("write-tree"), self.git("write-tree"))

    def test_write_tree_from_pygit_index_matches_git(self):
        self.init()
        self.write("a.txt", "a\n")
        self.write("d/b.txt", "b\n")
        os.symlink("a.txt", self.path("ln"))
        self.pg("add", ".")
        tree = self.pg("write-tree").strip()
        self.assertEqual(tree, self.git("write-tree").strip())
        self.assertEqual(self.git("ls-tree", "-r", tree).count("\n"), 3)

    def test_commit_tree_is_valid_for_git(self):
        self.init()
        self.write("a.txt", "a\n")
        self.pg("add", "a.txt")
        tree = self.pg("write-tree").strip()
        c1 = self.pg("commit-tree", tree, "-m", "root").strip()
        self.tick()
        c2 = self.pg("commit-tree", tree, "-p", c1, "-m", "second").strip()
        self.pg("update-ref", "refs/heads/main", c2)
        self.fsck()
        self.assertEqual(self.git("log", "--format=%s").split(), ["second", "root"])
        self.assertEqual(self.git("cat-file", "-p", c1),
                         f"tree {tree}\nauthor Test Author <author@example.com> 1700000000 +0800\n"
                         "committer Test Committer <committer@example.com> 1700000000 +0800\n\nroot\n")

    def test_commit_tree_multiple_parents_makes_merge(self):
        self.init()
        self.write("a", "a")
        self.pg("add", "a")
        tree = self.pg("write-tree").strip()
        p1 = self.pg("commit-tree", tree, "-m", "one").strip()
        p2 = self.pg("commit-tree", tree, "-m", "two").strip()
        m = self.pg("commit-tree", tree, "-p", p1, "-p", p2, "-m", "merge").strip()
        self.pg("update-ref", "HEAD", m)
        self.assertEqual(self.git("rev-list", "--parents", "-n1", m).split(), [m, p1, p2])
        self.fsck()

    def test_commit_tree_message_from_stdin(self):
        self.init()
        tree = self.git("write-tree").strip()
        c = self.pg("commit-tree", tree, input=b"from stdin\n").strip()
        self.assertEqual(self.git("log", "-1", "--format=%B", c).strip(), "from stdin")

    def test_commit_tree_two_messages_make_paragraphs(self):
        self.init()
        tree = self.git("write-tree").strip()
        c = self.pg("commit-tree", tree, "-m", "subject", "-m", "body").strip()
        self.assertEqual(self.git("log", "-1", "--format=%B", c), "subject\n\nbody\n\n")

    def test_commit_tree_rejects_non_tree(self):
        self.init()
        self.write("f", "x")
        blob = self.pg("hash-object", "-w", "f").strip()
        self.assertIn("not a tree", self.pg_fail("commit-tree", blob, "-m", "x"))

    def test_update_ref_and_delete(self):
        ids = self.make_history()
        self.pg("update-ref", "refs/heads/topic", ids[0])
        self.assertEqual(self.git("rev-parse", "topic").strip(), ids[0])
        self.pg("update-ref", "refs/tags/t", "HEAD~1")
        self.assertEqual(self.git("rev-parse", "t").strip(), ids[1])
        self.pg("update-ref", "-d", "refs/heads/topic")
        self.assertEqual(self.git("branch", "--list", "topic"), "")

    def test_update_ref_refuses_blob_on_branch(self):
        self.make_history()
        self.assertIn("non-commit", self.pg_fail("update-ref", "refs/heads/bad", "HEAD:a.txt"))

    def test_update_ref_rejects_bad_name(self):
        self.make_history()
        self.pg_fail("update-ref", "refs/heads/has space", "HEAD")
        self.pg_fail("update-ref", "notrefs/x", "HEAD")

    def test_symbolic_ref(self):
        self.make_history()
        self.assertEqual(self.pg("symbolic-ref", "HEAD").strip(), "refs/heads/main")
        self.pg("symbolic-ref", "HEAD", "refs/heads/other")
        self.assertEqual(self.git("symbolic-ref", "HEAD").strip(), "refs/heads/other")


class ListingTests(helpers.RepoCase):
    def setUp(self):
        super().setUp()
        self.init()
        self.write("a.txt", "a")
        self.write("z/中文.txt", "b")
        self.write("z/y/deep.txt", "c")
        self.write("x y.txt", "d")
        self.write("run.sh", "#!/bin/sh", exec_=True)
        os.symlink("a.txt", self.path("ln"))
        self.git("add", "-A")
        self.git_commit("c")

    def test_ls_tree_variants_match_git(self):
        for args in (["HEAD"], ["-r", "HEAD"], ["--name-only", "HEAD"], ["-r", "--name-only", "HEAD"], ["HEAD^{tree}"]):
            with self.subTest(args=args):
                self.assertEqual(self.pgb("ls-tree", *args), self.gitb("ls-tree", *args))

    def test_ls_tree_z(self):
        self.assertEqual(self.pgb("ls-tree", "-r", "-z", "HEAD"), self.gitb("ls-tree", "-r", "-z", "HEAD"))

    def test_ls_files_variants_match_git(self):
        for args in ([], ["-s"], ["--stage"], ["-z"], ["-s", "-z"]):
            with self.subTest(args=args):
                self.assertEqual(self.pgb("ls-files", *args), self.gitb("ls-files", *args))

    def test_ls_tree_accepts_a_commit_or_tree_id(self):
        tree = self.git("rev-parse", "HEAD^{tree}").strip()
        self.assertEqual(self.pgb("ls-tree", tree), self.gitb("ls-tree", tree))

    def test_ls_tree_rejects_blob(self):
        self.assertIn("not a tree", self.pg_fail("ls-tree", "HEAD:a.txt"))
