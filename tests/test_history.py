"""log / show / tag / cat-file on repositories created by real git, and the reverse."""
import helpers


class GitMadeHistory(helpers.RepoCase):
    def setUp(self):
        super().setUp()
        self.init()
        self.write("a.txt", "one\n")
        self.git("add", "-A")
        self.git_commit("first commit")
        self.write("a.txt", "one\ntwo\n")
        self.write("dir/b.txt", "bee\n")
        self.git("add", "-A")
        self.git_commit("second commit\n\nWith a body paragraph.\n\nAnd another one.")
        self.git("switch", "-q", "-c", "side")
        self.write("side.txt", "side\n")
        self.git("add", "-A")
        self.git_commit("side work")
        self.git("switch", "-q", "main")
        self.write("main2.txt", "m\n")
        self.git("add", "-A")
        self.git_commit("main work")
        self.tick()
        self.git("merge", "-q", "--no-ff", "-m", "Merge branch 'side'", "side")
        self.write("a.txt", "one\ntwo\nthree\n")
        self.git("add", "-A")
        self.git_commit("after merge: 你好 🙂")
        self.tick()
        self.git("tag", "-a", "-m", "release 1.0", "v1.0")
        self.git("tag", "light", "HEAD~1")

    def test_log_matches_git_exactly(self):
        self.assertEqual(self.pgb("log"), self.gitb("log", "--no-decorate"))

    def test_log_oneline(self):
        self.assertEqual(self.pgb("log", "--oneline"), self.gitb("log", "--oneline", "--no-decorate"))

    def test_log_limit(self):
        self.assertEqual(self.pgb("log", "-n", "2"), self.gitb("log", "-n", "2", "--no-decorate"))
        self.assertEqual(self.pg("log", "-n", "1", "--oneline").count("\n"), 1)

    def test_log_from_revision(self):
        self.assertEqual(self.pgb("log", "--oneline", "side"), self.gitb("log", "--oneline", "side"))
        self.assertEqual(self.pgb("log", "--oneline", "HEAD~2"), self.gitb("log", "--oneline", "HEAD~2"))

    def test_log_from_tag(self):
        self.assertEqual(self.pgb("log", "--oneline", "v1.0"), self.gitb("log", "--oneline", "v1.0"))

    def test_merge_commit_shows_merge_line(self):
        text = self.pg("log", "-n", "2")
        self.assertIn("Merge: ", text)

    def test_log_decorations(self):
        first = self.pg("log", "--oneline", "--decorate", "-n", "1")
        self.assertEqual(first, self.git("log", "--oneline", "--decorate", "-n", "1"))
        self.assertIn("HEAD -> main", first)
        self.assertIn("tag: v1.0", first)

    def test_log_graph_lists_every_commit_once(self):
        text = self.pg("log", "--graph", "--oneline")
        self.assertEqual(text.count("*"), 6)
        self.assertIn("|", text)

    def test_cat_file_every_reachable_object(self):
        for line in self.git("rev-list", "--objects", "--all").splitlines():
            sha = line.split()[0]
            for flag in ("-t", "-s", "-p"):
                self.assertEqual(self.pgb("cat-file", flag, sha), self.gitb("cat-file", flag, sha), (sha, flag))

    def test_ls_tree_recursive_at_each_commit(self):
        for rev in ("HEAD", "HEAD~1", "HEAD~2", "side", "HEAD~1^2"):
            self.assertEqual(self.pgb("ls-tree", "-r", rev), self.gitb("ls-tree", "-r", rev), rev)

    def test_show_commit_matches_git(self):
        for rev in ("HEAD", "HEAD~2", "side", "HEAD~3"):
            with self.subTest(rev=rev):
                self.assertEqual(self.pgb("show", rev), self.gitb("show", "--no-decorate", rev))

    def test_show_annotated_tag(self):
        self.assertEqual(self.pgb("show", "v1.0"), self.gitb("show", "--no-decorate", "v1.0"))

    def test_show_blob_and_tree(self):
        self.assertEqual(self.pgb("show", "HEAD:a.txt"), self.gitb("show", "HEAD:a.txt"))
        self.assertEqual(self.pgb("show", "HEAD:dir"), self.gitb("show", "HEAD:dir"))

    def test_show_root_commit(self):
        root = self.git("rev-list", "--max-parents=0", "HEAD").strip()
        self.assertEqual(self.pgb("show", root), self.gitb("show", "--no-decorate", root))

    def test_rev_parse_matches_for_every_commit(self):
        for sha in self.git("rev-list", "--all").split():
            self.assertEqual(self.pg("rev-parse", sha[:9]).strip(), sha)

    def test_branch_and_tag_listing(self):
        self.assertEqual(self.pg("branch"), self.git("branch"))
        self.assertEqual(self.pg("tag"), self.git("tag"))

    def test_status_of_repository_created_by_git(self):
        self.assertEqual(self.pg("status", "--porcelain"), "")

    def test_pygit_can_continue_history_created_by_git(self):
        self.write("new.txt", "n\n")
        self.pg("add", "new.txt")
        self.pg_commit("continued by pygit")
        self.fsck()
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "continued by pygit")
        self.assertEqual(self.git("rev-list", "--count", "HEAD").strip(), "7")

    def test_commit_with_gpgsig_style_header_is_parsed(self):
        tree = self.git("rev-parse", "HEAD^{tree}").strip()
        raw = (f"tree {tree}\nauthor A <a@b.c> 1700000000 +0000\ncommitter A <a@b.c> 1700000000 +0000\n"
               "gpgsig -----BEGIN PGP SIGNATURE-----\n \n fake\n -----END PGP SIGNATURE-----\n\nsigned commit\n")
        sha = self.git("hash-object", "-t", "commit", "-w", "--stdin", input=raw.encode()).strip()
        self.assertEqual(self.pg("log", "-n", "1", "--oneline", sha), f"{sha[:7]} signed commit\n")
        self.assertIn("signed commit", self.pg("show", sha))


class PygitMadeHistory(helpers.RepoCase):
    def setUp(self):
        super().setUp()
        self.init("pygit")

    def test_pygit_history_is_readable_by_git_log(self):
        for i in range(4):
            self.write("f.txt", f"v{i}\n")
            self.pg("add", "f.txt")
            self.pg_commit(f"change {i}")
        self.fsck()
        self.assertEqual(self.pg("log"), self.git("log"))
        self.assertEqual(self.pg("log", "--oneline"), self.git("log", "--oneline"))
        self.assertEqual(self.pg("show"), self.git("show"))

    def test_pygit_merge_commit_via_plumbing(self):
        self.write("a", "a\n")
        self.pg("add", "a")
        self.pg_commit("base")
        base = self.pg("rev-parse", "HEAD").strip()
        self.pg("branch", "other")
        self.write("b", "b\n")
        self.pg("add", "b")
        self.pg_commit("on main")
        self.pg("switch", "other")
        self.write("c", "c\n")
        self.pg("add", "c")
        self.pg_commit("on other")
        other = self.pg("rev-parse", "HEAD").strip()
        self.pg("switch", "main")
        main = self.pg("rev-parse", "HEAD").strip()
        self.write("c", "c\n")
        self.pg("add", "c")
        tree = self.pg("write-tree").strip()
        self.tick()
        merge = self.pg("commit-tree", tree, "-p", main, "-p", other, "-m", "merge other").strip()
        self.pg("update-ref", "HEAD", merge)
        self.fsck()
        self.assertEqual(self.pg("log", "--oneline"), self.git("log", "--oneline"))
        self.assertEqual(self.pg("log"), self.git("log"))
        self.assertEqual(self.git("rev-list", "--parents", "-n1", "HEAD").split(), [merge, main, other])
        self.assertEqual(self.pg("rev-parse", "HEAD^2").strip(), other)
        self.assertEqual(self.pg("rev-parse", "HEAD^^").strip(), base)

    def test_pygit_annotated_tag_shown_by_git(self):
        self.write("a", "a")
        self.pg("add", "a")
        self.pg_commit("c")
        self.tick()
        self.pg("tag", "-a", "v1", "-m", "first release")
        self.assertEqual(self.pg("show", "v1"), self.git("show", "--no-decorate", "v1"))
        self.assertIn("first release", self.git("show", "v1"))
        self.fsck()

    def test_log_graph_linear(self):
        for i in range(3):
            self.write("f", f"{i}\n")
            self.pg("add", "f")
            self.pg_commit(f"c{i}")
        lines = self.pg("log", "--graph").splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(ln.startswith("* ") for ln in lines))

    def test_log_on_empty_repository(self):
        self.assertIn("does not have any commits yet", self.pg_fail("log"))
