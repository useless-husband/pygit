import os
import zlib

import helpers
from pygit.objects import (Commit, Tag, clean_message, decode_object, encode_object, hash_bytes,
                           parse_ident, parse_tree, serialize_tree)
from pygit.odb import ObjectDB
from pygit.util import GitError, format_date, parse_date, quote_path


class HashTests(helpers.RepoCase):
    def setUp(self):
        super().setUp()
        self.init()

    def test_known_blob_hash(self):
        self.assertEqual(hash_bytes("blob", b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")

    def test_empty_blob_and_empty_tree(self):
        self.assertEqual(hash_bytes("blob", b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")
        self.assertEqual(hash_bytes("tree", b""), "4b825dc642cb6eb9a060e54bf8d69288fbee4904")

    def test_hash_object_matches_git_for_many_contents(self):
        samples = {
            "empty": b"", "newline": b"\n", "text": b"hello world\n", "no-eol": b"abc",
            "crlf": b"a\r\nb\r\n", "binary": bytes(range(256)) * 3, "nul": b"\0\0\0",
            "unicode": "你好，世界\n".encode(), "emoji": "🙂\n".encode(),
            "big": b"0123456789abcdef" * 70000,
        }
        for name, data in samples.items():
            with self.subTest(name):
                self.write("f.bin", data)
                self.assertEqual(self.pg("hash-object", "f.bin"), self.git("hash-object", "f.bin"))

    def test_hash_object_stdin_matches_git(self):
        data = b"from stdin\n"
        a = self.pg("hash-object", "--stdin", input=data)
        b = self.git("hash-object", "--stdin", input=data)
        self.assertEqual(a, b)

    def test_hash_object_other_types_match_git(self):
        for t in ("blob", "tree", "commit", "tag"):
            with self.subTest(t):
                data = b"arbitrary bytes for " + t.encode()
                expected = hash_bytes(t, data)
                got = self.git("hash-object", "--literally", "-t", t, "--stdin", input=data).strip()
                self.assertEqual(expected, got)

    def test_hash_object_w_is_readable_by_git(self):
        self.write("f.txt", "stored by pygit\n")
        sha = self.pg("hash-object", "-w", "f.txt").strip()
        self.assertEqual(self.git("cat-file", "-p", sha), "stored by pygit\n")
        self.assertEqual(self.git("cat-file", "-t", sha).strip(), "blob")
        self.fsck()

    def test_hash_object_without_w_writes_nothing(self):
        self.write("f.txt", "not stored\n")
        sha = self.pg("hash-object", "f.txt").strip()
        self.assertFalse(os.path.exists(self.path(f".git/objects/{sha[:2]}/{sha[2:]}")))

    def test_hash_object_invalid_type(self):
        self.write("f.txt", "x")
        self.assertIn("invalid object type", self.pg_fail("hash-object", "-t", "banana", "f.txt"))


class TreeTests(helpers.RepoCase):
    def test_roundtrip(self):
        entries = [(0o100644, "a.txt", "a" * 40), (0o040000, "dir", "b" * 40), (0o100755, "run.sh", "c" * 40)]
        parsed = parse_tree(serialize_tree(entries))
        self.assertEqual([(e.mode, e.name, e.sha) for e in parsed],
                         sorted(entries, key=lambda e: e[1]))

    def test_directory_sorts_as_if_name_ends_with_slash(self):
        self.init()
        sha = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
        entries = [(0o100644, "a.b", sha), (0o040000, "a", "4b825dc642cb6eb9a060e54bf8d69288fbee4904"),
                   (0o100644, "a-b", sha), (0o100644, "a0", sha)]
        self.git("hash-object", "-w", "--stdin", input=b"")
        listing = "".join(f"{m:06o} {'tree' if m == 0o040000 else 'blob'} {s}\t{n}\n" for m, n, s in entries)
        git_sha = self.git("mktree", input=listing.encode()).strip()
        self.assertEqual(hash_bytes("tree", serialize_tree(entries)), git_sha)

    def test_utf8_names(self):
        sha = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
        parsed = parse_tree(serialize_tree([(0o100644, "中文.txt", sha)]))
        self.assertEqual(parsed[0].name, "中文.txt")

    def test_truncated_tree_is_an_error(self):
        with self.assertRaises(GitError):
            parse_tree(b"100644 name\0" + b"x" * 5)


class CommitTests(helpers.RepoCase):
    RAW = (b"tree " + b"a" * 40 + b"\nparent " + b"b" * 40 + b"\nparent " + b"c" * 40 +
           b"\nauthor Ann <ann@x.org> 1700000000 +0800\ncommitter Bob <bob@x.org> 1700000060 -0530\n"
           b"gpgsig -----BEGIN PGP SIGNATURE-----\n \n abc\n -----END PGP SIGNATURE-----\n"
           b"\nSubject line\n\nBody text\n")

    def test_parse_fields(self):
        c = Commit.parse(self.RAW)
        self.assertEqual(c.tree, "a" * 40)
        self.assertEqual(c.parents, ["b" * 40, "c" * 40])
        self.assertEqual(c.author_ident.name, "Ann")
        self.assertEqual(c.committer_ident.tz, "-0530")
        self.assertEqual(c.subject, "Subject line")

    def test_serialize_roundtrip_keeps_multiline_headers(self):
        self.assertEqual(Commit.parse(self.RAW).serialize(), self.RAW)

    def test_message_without_trailing_newline(self):
        raw = b"tree " + b"a" * 40 + b"\nauthor A <a@b> 1 +0000\ncommitter A <a@b> 1 +0000\n\nno newline"
        self.assertEqual(Commit.parse(raw).message, "no newline")
        self.assertEqual(Commit.parse(raw).serialize(), raw)

    def test_commit_without_tree_is_error(self):
        with self.assertRaises(GitError):
            Commit.parse(b"author x\n\nmsg")

    def test_tag_roundtrip(self):
        raw = (b"object " + b"a" * 40 + b"\ntype commit\ntag v1.0\ntagger T <t@x> 1700000000 +0000\n\nrelease\n")
        t = Tag.parse(raw)
        self.assertEqual((t.object, t.type, t.name), ("a" * 40, "commit", "v1.0"))
        self.assertEqual(t.serialize(), raw)

    def test_parse_ident_with_odd_name(self):
        i = parse_ident("Jane Q. Public <jane@example.com> 1234567890 -0700")
        self.assertEqual((i.name, i.email, i.ts, i.tz), ("Jane Q. Public", "jane@example.com", 1234567890, "-0700"))

    def test_clean_message(self):
        self.assertEqual(clean_message("\n\nhello  \n\n\n\nworld\n\n"), "hello\n\nworld\n")
        self.assertEqual(clean_message("   \n"), "")


class DecodeTests(helpers.RepoCase):
    def test_roundtrip(self):
        self.assertEqual(decode_object(encode_object("blob", b"xyz")), ("blob", b"xyz"))

    def test_size_mismatch(self):
        with self.assertRaises(GitError):
            decode_object(b"blob 10\0abc")

    def test_unknown_type(self):
        with self.assertRaises(GitError):
            decode_object(b"banana 1\0a")

    def test_missing_header(self):
        with self.assertRaises(GitError):
            decode_object(b"no header here")


class OdbTests(helpers.RepoCase):
    def setUp(self):
        super().setUp()
        self.odb = ObjectDB(os.path.join(self.dir, "objects"))

    def test_write_then_read(self):
        sha = self.odb.write("blob", b"data")
        self.assertEqual(self.odb.read(sha), ("blob", b"data"))
        self.assertTrue(self.odb.exists(sha))

    def test_write_is_idempotent_and_readonly(self):
        sha = self.odb.write("blob", b"data")
        self.assertEqual(self.odb.write("blob", b"data"), sha)
        mode = os.stat(os.path.join(self.dir, "objects", sha[:2], sha[2:])).st_mode & 0o777
        self.assertEqual(mode, 0o444)

    def test_missing_object(self):
        with self.assertRaises(GitError):
            self.odb.read("0" * 40)
        self.assertFalse(self.odb.exists("0" * 40))

    def test_prefix_lookup(self):
        sha = self.odb.write("blob", b"prefix me")
        self.assertEqual(self.odb.find_prefix(sha[:6]), [sha])
        self.assertEqual(self.odb.find_prefix("ffffff"), [])

    def test_corrupt_zlib(self):
        sha = self.odb.write("blob", b"data")
        p = os.path.join(self.dir, "objects", sha[:2], sha[2:])
        os.chmod(p, 0o644)
        with open(p, "wb") as f:
            f.write(b"not zlib")
        with self.assertRaises(GitError):
            self.odb.read(sha)

    def test_invalid_id(self):
        with self.assertRaises(GitError):
            self.odb.read("xyz")

    def test_object_written_by_pygit_is_read_by_git(self):
        self.init()
        odb = ObjectDB(self.path(".git/objects"))
        sha = odb.write("blob", b"cross check\n")
        self.assertEqual(self.git("cat-file", "-p", sha), "cross check\n")

    def test_object_written_by_git_is_read_by_pygit(self):
        self.init()
        self.write("g.txt", "written by git\n")
        sha = self.git("hash-object", "-w", "g.txt").strip()
        self.assertEqual(ObjectDB(self.path(".git/objects")).read(sha), ("blob", b"written by git\n"))


class UtilTests(helpers.RepoCase):
    def test_quote_path(self):
        self.assertEqual(quote_path("plain.txt"), "plain.txt")
        self.assertEqual(quote_path("中.txt"), '"\\344\\270\\255.txt"')
        self.assertEqual(quote_path("a b"), "a b")
        self.assertEqual(quote_path("a b", True), '"a b"')
        self.assertEqual(quote_path('q"uote'), '"q\\"uote"')
        self.assertEqual(quote_path("tab\there"), '"tab\\there"')

    def test_parse_date_formats(self):
        self.assertEqual(parse_date("1700000000 +0800"), (1700000000, "+0800"))
        self.assertEqual(parse_date("@1700000000 -0530"), (1700000000, "-0530"))
        self.assertEqual(parse_date("2023-11-14T22:13:20+00:00"), (1700000000, "+0000"))
        self.assertEqual(parse_date("2023-11-15 06:13:20 +0800"), (1700000000, "+0800"))
        self.assertEqual(parse_date("2023-11-14T22:13:20Z"), (1700000000, "+0000"))
        with self.assertRaises(GitError):
            parse_date("yesterday-ish")

    def test_format_date(self):
        self.assertEqual(format_date(1700000000, "+0800"), "Wed Nov 15 06:13:20 2023 +0800")
        self.assertEqual(format_date(1700000000, "-0500"), "Tue Nov 14 17:13:20 2023 -0500")
