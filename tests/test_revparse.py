import helpers


class RevParseTests(helpers.RepoCase):
    def setUp(self):
        super().setUp()
        self.init()
        # history: c0 - c1 - c2 (main), side branch from c1 merged with --no-ff
        for i in range(3):
            self.write("a.txt", f"v{i}\n")
            self.write("dir/x.txt", f"x{i}\n")
            self.git("add", "-A")
            self.git_commit(f"c{i}")
            if i == 1:
                self.git("branch", "side")
        self.git("switch", "-q", "side")
        self.write("side.txt", "s\n")
        self.git("add", "-A")
        self.git_commit("side work")
        self.git("switch", "-q", "main")
        self.tick()
        self.git("merge", "-q", "--no-ff", "-m", "merge side", "side")
        self.git("tag", "light")
        self.tick()
        self.git("tag", "-a", "-m", "annotated", "v1")
        self.git("branch", "feature/deep")

    def assert_specs(self, specs):
        for spec in specs:
            with self.subTest(spec=spec):
                self.assertEqual(self.pg("rev-parse", spec), self.git("rev-parse", spec))

    def test_head_and_branches(self):
        self.assert_specs(["HEAD", "main", "side", "refs/heads/main", "heads/main", "feature/deep", "@"])

    def test_parent_syntax(self):
        self.assert_specs(["HEAD^", "HEAD^1", "HEAD^2", "HEAD^0", "HEAD~1", "HEAD~2", "HEAD~0", "HEAD~3",
                           "HEAD^^", "HEAD^2^", "HEAD~1^"])

    def test_tags(self):
        self.assert_specs(["light", "v1", "v1^{}", "v1^{commit}", "v1^{tree}", "v1~1", "refs/tags/v1", "v1^0"])

    def test_peel_to_tree_and_blob_paths(self):
        self.assert_specs(["HEAD^{tree}", "HEAD:a.txt", "HEAD:dir", "HEAD:dir/x.txt", "HEAD~1:a.txt", "main:side.txt"])

    def test_short_and_full_hashes(self):
        full = self.git("rev-parse", "HEAD~1").strip()
        for n in (4, 7, 12, 40):
            self.assertEqual(self.pg("rev-parse", full[:n]).strip(), full)
        self.assertEqual(self.pg("rev-parse", full[:8] + "~1").strip(), self.git("rev-parse", "HEAD~2").strip())

    def test_short_output(self):
        self.assertEqual(self.pg("rev-parse", "--short", "HEAD").strip(), self.git("rev-parse", "--short=7", "HEAD").strip())

    def test_multiple_args(self):
        self.assertEqual(self.pg("rev-parse", "HEAD", "HEAD~1"), self.git("rev-parse", "HEAD", "HEAD~1"))

    def test_unknown_revision(self):
        self.assertIn("unknown revision", self.pg_fail("rev-parse", "nope"))

    def test_too_many_ancestors(self):
        self.assertIn("does not exist", self.pg_fail("rev-parse", "HEAD~50"))

    def test_bad_suffix(self):
        self.pg_fail("rev-parse", "HEAD^x")

    def test_missing_path_in_tree(self):
        self.assertIn("does not exist", self.pg_fail("rev-parse", "HEAD:nothing.txt"))

    def test_unborn_head(self):
        self.tmp2 = self.dir + "-empty"
        import os
        os.mkdir(self.tmp2)
        p = self.run_cmd(["git", "init", "-q", "-b", "main"], cwd=self.tmp2)
        err = self.pg_fail("rev-parse", "HEAD", cwd=self.tmp2)
        self.assertIn("unknown revision", err)

    def test_toplevel_and_git_dir(self):
        self.assertEqual(self.pg("rev-parse", "--show-toplevel").strip(), self.dir)
        self.assertEqual(self.pg("rev-parse", "--git-dir").strip(), ".git")
