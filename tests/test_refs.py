import os

import helpers
from pygit import refs as R
from pygit.util import GitError


class RefStoreTests(helpers.RepoCase):
    def setUp(self):
        super().setUp()
        self.init()
        self.gd = self.path(".git")

    def test_write_and_read_ref(self):
        R.write_ref(self.gd, "refs/heads/x", "a" * 40)
        self.assertEqual(R.read_ref(self.gd, "refs/heads/x"), "a" * 40)

    def test_head_symbolic_write_follows_to_branch(self):
        R.write_ref(self.gd, "HEAD", "b" * 40)
        self.assertEqual(R.read_ref(self.gd, "refs/heads/main"), "b" * 40)
        self.assertEqual(R.read_symref(self.gd, "HEAD"), "refs/heads/main")

    def test_missing_ref_is_none(self):
        self.assertIsNone(R.read_ref(self.gd, "refs/heads/nope"))

    def test_nested_branch_names(self):
        R.write_ref(self.gd, "refs/heads/feature/login", "c" * 40)
        self.assertEqual(list(R.list_refs(self.gd, "refs/heads/")), ["refs/heads/feature/login"])

    def test_file_directory_conflict(self):
        R.write_ref(self.gd, "refs/heads/a", "c" * 40)
        with self.assertRaises(GitError):
            R.write_ref(self.gd, "refs/heads/a/b", "c" * 40)

    def test_packed_refs_are_read_and_loose_wins(self):
        with open(os.path.join(self.gd, "packed-refs"), "w") as f:
            f.write("# pack-refs with: peeled fully-peeled sorted \n" + "1" * 40 + " refs/heads/old\n"
                    + "2" * 40 + " refs/tags/v1\n^" + "3" * 40 + "\n")
        R.write_ref(self.gd, "refs/heads/old", "4" * 40)
        self.assertEqual(R.read_ref(self.gd, "refs/tags/v1"), "2" * 40)
        self.assertEqual(R.list_refs(self.gd, "refs/heads/")["refs/heads/old"], "4" * 40)
        self.assertEqual(list(R.list_refs(self.gd, "refs/tags/")), ["refs/tags/v1"])

    def test_delete_removes_loose_and_packed_entries(self):
        with open(os.path.join(self.gd, "packed-refs"), "w") as f:
            f.write("# header\n" + "2" * 40 + " refs/tags/v1\n^" + "3" * 40 + "\n" + "5" * 40 + " refs/tags/v2\n")
        R.delete_ref(self.gd, "refs/tags/v1")
        self.assertIsNone(R.read_ref(self.gd, "refs/tags/v1"))
        self.assertEqual(R.read_ref(self.gd, "refs/tags/v2"), "5" * 40)
        with open(os.path.join(self.gd, "packed-refs")) as f:
            self.assertNotIn("^" + "3" * 40, f.read())

    def test_delete_prunes_empty_directories(self):
        R.write_ref(self.gd, "refs/heads/topic/one", "c" * 40)
        R.delete_ref(self.gd, "refs/heads/topic/one")
        self.assertFalse(os.path.exists(os.path.join(self.gd, "refs/heads/topic")))
        self.assertTrue(os.path.isdir(os.path.join(self.gd, "refs/heads")))

    def test_valid_ref_names(self):
        good = ["refs/heads/main", "refs/heads/feature/x-1", "refs/tags/v1.0", "refs/heads/中文"]
        bad = ["", "refs/heads/a..b", "refs/heads/a b", "refs/heads/.hidden", "refs/heads/x.lock",
               "refs/heads/a~1", "refs/heads/a^", "refs/heads/a:b", "refs/heads/end/", "-dash",
               "refs/heads/a@{1}", "refs/heads/a*", "refs/heads//x"]
        for n in good:
            self.assertTrue(R.valid_ref_name(n), n)
        for n in bad:
            self.assertFalse(R.valid_ref_name(n), n)

    def test_git_reads_refs_written_by_pygit(self):
        self.write("f", "x")
        self.git("add", "f")
        self.git_commit("c")
        sha = self.git("rev-parse", "HEAD").strip()
        R.write_ref(self.gd, "refs/heads/from-pygit", sha)
        self.assertEqual(self.git("rev-parse", "from-pygit").strip(), sha)
