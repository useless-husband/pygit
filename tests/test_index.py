import hashlib
import os
import stat
import struct

import helpers
from pygit.index import IndexEntry, parse_index, serialize_index
from pygit.util import GitError

SHA = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


def entry(path, **kw):
    return IndexEntry(path, kw.pop("sha", SHA), kw.pop("mode", 0o100644), **kw)


def reseal(body):
    return body + hashlib.sha1(body).digest()


class IndexFormatTests(helpers.RepoCase):
    def test_roundtrip_all_fields(self):
        e = entry("dir/file.txt", size=1234, ctime_s=1, ctime_ns=2, mtime_s=3, mtime_ns=4, dev=5, ino=6, uid=7, gid=8)
        back = parse_index(serialize_index([e])).entries["dir/file.txt"]
        for f in ("path", "sha", "mode", "size", "ctime_s", "ctime_ns", "mtime_s", "mtime_ns", "dev", "ino", "uid", "gid"):
            self.assertEqual(getattr(back, f), getattr(e, f), f)

    def test_header_and_trailer(self):
        data = serialize_index([entry("a")])
        self.assertEqual(data[:4], b"DIRC")
        self.assertEqual(struct.unpack(">II", data[4:12]), (2, 1))
        self.assertEqual(hashlib.sha1(data[:-20]).digest(), data[-20:])

    def test_entries_are_padded_to_8_bytes(self):
        for n in range(1, 25):
            with self.subTest(name_len=n):
                data = serialize_index([entry("x" * n)])
                body = len(data) - 12 - 20
                self.assertEqual(body % 8, 0)
                self.assertGreaterEqual(body, 62 + n + 1)      # at least one NUL after the name
                self.assertLess(body, 62 + n + 1 + 8)

    def test_exact_padding_for_known_lengths(self):
        self.assertEqual(len(serialize_index([entry("a")])), 12 + 64 + 20)        # 62+1 -> 64
        self.assertEqual(len(serialize_index([entry("ab")])), 12 + 72 + 20)       # 62+2 -> 64 needs 8 NULs

    def test_entries_sorted_by_bytes(self):
        data = serialize_index([entry("b"), entry("a/b"), entry("a.b"), entry("a")])
        names = [e.path for e in parse_index(data).sorted_entries()]
        self.assertEqual(names, ["a", "a.b", "a/b", "b"])

    def test_very_long_path(self):
        path = "/".join(["d" * 200] * 25)      # 5000 bytes, more than the 12-bit length field
        back = parse_index(serialize_index([entry(path)]))
        self.assertIn(path, back.entries)

    def test_bad_signature(self):
        with self.assertRaises(GitError):
            parse_index(b"XXXX" + b"\0" * 40)

    def test_checksum_mismatch(self):
        data = bytearray(serialize_index([entry("a")]))
        data[20] ^= 1
        with self.assertRaisesRegex(GitError, "checksum"):
            parse_index(bytes(data))

    def test_version_4_gives_clear_error(self):
        body = b"DIRC" + struct.pack(">II", 4, 0)
        with self.assertRaisesRegex(GitError, "version 4"):
            parse_index(reseal(body))

    def test_optional_extension_is_skipped(self):
        data = serialize_index([entry("a")])[:-20]
        ext = b"TREE" + struct.pack(">I", 5) + b"12345"
        idx = parse_index(reseal(data + ext))
        self.assertEqual(list(idx.entries), ["a"])

    def test_required_extension_is_rejected(self):
        data = serialize_index([entry("a")])[:-20]
        with self.assertRaisesRegex(GitError, "link"):
            parse_index(reseal(data + b"link" + struct.pack(">I", 0)))

    def test_truncated_file(self):
        with self.assertRaises(GitError):
            parse_index(b"DIRC")


class IndexInteropTests(helpers.RepoCase):
    def populate(self):
        self.init()
        self.write("a.txt", "a\n")
        self.write("dir/b.txt", "b\n")
        self.write("dir/sub/c.txt", "c\n")
        self.write("run.sh", "#!/bin/sh\n", exec_=True)
        self.write("中文/檔案.txt", "unicode\n")
        self.write("with space.txt", "space\n")
        self.write("empty", "")
        os.symlink("a.txt", self.path("link"))

    def test_git_add_then_pygit_ls_files_stage(self):
        self.populate()
        self.git("add", "-A")
        self.assertEqual(self.pgb("ls-files", "--stage", "-z"), self.gitb("ls-files", "--stage", "-z"))

    def test_git_index_after_commit_has_tree_extension_and_is_readable(self):
        self.populate()
        self.git("add", "-A")
        self.git_commit("c")
        self.assertIn(b"TREE", self.read(".git/index"))
        self.assertEqual(self.pgb("ls-files", "--stage", "-z"), self.gitb("ls-files", "--stage", "-z"))

    def test_pygit_add_then_git_ls_files_stage(self):
        self.populate()
        self.pg("add", ".")
        self.assertEqual(self.gitb("ls-files", "--stage", "-z"), self.pgb("ls-files", "--stage", "-z"))
        stage = self.git("ls-files", "--stage")
        self.assertIn("100755", stage)
        self.assertIn("120000", stage)

    def test_modes_recorded(self):
        self.populate()
        self.pg("add", ".")
        lines = {ln.split("\t")[1]: ln.split()[0] for ln in self.git("-c", "core.quotepath=false", "ls-files", "-s").splitlines()}
        self.assertEqual(lines["run.sh"], "100755")
        self.assertEqual(lines["link"], "120000")
        self.assertEqual(lines["a.txt"], "100644")

    def test_git_accepts_pygit_index_status_is_clean_after_commit(self):
        self.populate()
        self.pg("add", ".")
        self.pg_commit("all")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("diff", "--cached"), "")
        self.fsck()

    def test_git_reads_stat_cache_written_by_pygit(self):
        self.populate()
        self.pg("add", ".")
        self.pg_commit("all")
        self.assertEqual(self.git("diff"), "")
        self.assertEqual(self.git("ls-files", "-m"), "")

    def test_intent_to_add_creates_v3_index_that_pygit_reads(self):
        self.init()
        self.write("a.txt", "a\n")
        self.git("add", "-N", "a.txt")
        version = struct.unpack(">I", self.read(".git/index")[4:8])[0]
        self.assertEqual(version, 3)
        self.assertEqual(self.pg("ls-files").strip(), "a.txt")

    def test_index_v4_from_git_is_a_clear_error(self):
        self.populate()
        self.git("add", "-A")
        self.git("update-index", "--index-version", "4")
        self.assertIn("version 4", self.pg_fail("ls-files"))

    def test_stat_data_matches_lstat(self):
        self.populate()
        self.pg("add", "a.txt")
        e = parse_index(self.read(".git/index")).entries["a.txt"]
        st = os.lstat(self.path("a.txt"))
        self.assertEqual((e.size, e.ino, e.mtime_s), (st.st_size, st.st_ino & 0xFFFFFFFF, int(st.st_mtime)))
        self.assertTrue(stat.S_ISREG(st.st_mode))

    def test_unicode_and_space_names_roundtrip_through_pygit_index(self):
        self.populate()
        self.pg("add", ".")
        names = self.gitb("ls-files", "-z").split(b"\0")
        self.assertIn("中文/檔案.txt".encode(), names)
        self.assertIn(b"with space.txt", names)
