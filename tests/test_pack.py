"""Reading repositories that real git has packed (git gc / repack / pack-refs)."""
import os
import struct
import zlib

import helpers
from pygit.odb import ObjectDB
from pygit.pack import PackFile, apply_delta
from pygit.util import GitError


def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


class DeltaTests(helpers.RepoCase):
    def test_insert_only(self):
        delta = varint(0) + varint(3) + bytes([3]) + b"abc"
        self.assertEqual(apply_delta(b"", delta), b"abc")

    def test_copy_and_insert(self):
        base = b"hello world"
        # copy offset 0 size 5, insert "!!", copy offset 5 size 6
        delta = varint(11) + varint(13) + bytes([0x90, 5]) + bytes([2]) + b"!!" + bytes([0x91, 5, 6])
        self.assertEqual(apply_delta(base, delta), b"hello!! world")

    def test_copy_with_two_byte_offset_and_default_size(self):
        base = bytes(range(256)) * 2                 # 512 bytes
        delta = varint(512) + varint(16) + bytes([0x93, 0x00, 0x01, 16])    # offset 0x100, size 16
        self.assertEqual(apply_delta(base, delta), base[256:272])

    def test_wrong_base_size(self):
        with self.assertRaises(GitError):
            apply_delta(b"abc", varint(5) + varint(0))

    def test_result_size_mismatch(self):
        with self.assertRaises(GitError):
            apply_delta(b"", varint(0) + varint(5) + bytes([1]) + b"a")

    def test_copy_out_of_range(self):
        with self.assertRaises(GitError):
            apply_delta(b"abc", varint(3) + varint(10) + bytes([0x90, 10]))

    def test_opcode_zero_is_reserved(self):
        with self.assertRaises(GitError):
            apply_delta(b"", varint(0) + varint(0) + b"\0")


class PackedRepoTests(helpers.RepoCase):
    def setUp(self):
        super().setUp()
        self.init()
        lines = [f"line number {i} of the shared file\n" for i in range(200)]
        for i in range(25):
            lines[(i * 7) % 200] = f"edited in revision {i}\n"
            self.write("big.txt", "".join(lines))
            self.write(f"small/{i % 5}.txt", f"small {i}\n")
            self.git("add", "-A")
            self.git_commit(f"revision {i}")
        self.git("branch", "old", "HEAD~10")
        self.git("tag", "light", "HEAD~3")
        self.git("tag", "-a", "-m", "annotated", "v1", "HEAD~5")

    def repack(self, *extra, config=()):
        args = []
        for c in config:
            args += ["-c", c]
        self.git(*args, "repack", "-a", "-d", "-f", "-q", "--depth=50", "--window=50", *extra)
        self.git("prune-packed")

    def pack_object_kinds(self):
        kinds = {}
        pdir = self.path(".git/objects/pack")
        for fn in os.listdir(pdir):
            if fn.endswith(".idx"):
                pack = PackFile(os.path.join(pdir, fn))
                for i in range(pack.count):
                    kind = (pack._pack[pack._offset_at(i)] >> 4) & 7
                    kinds[kind] = kinds.get(kind, 0) + 1
                pack.close()
        return kinds

    def loose_objects(self):
        base = self.path(".git/objects")
        return [d for d in os.listdir(base) if len(d) == 2]

    def all_objects_match_git(self):
        shas = [ln.split()[0] for ln in self.git("rev-list", "--objects", "--all").splitlines()]
        odb = ObjectDB(self.path(".git/objects"))
        self.addCleanup(odb.close)
        batch = self.gitb("cat-file", "--batch", input=("\n".join(shas) + "\n").encode())
        pos = 0
        for sha in shas:
            eol = batch.index(b"\n", pos)
            _, kind, size = batch[pos:eol].decode().split()
            data = batch[eol + 1:eol + 1 + int(size)]
            pos = eol + 1 + int(size) + 1
            self.assertEqual(odb.read(sha), (kind, data), sha)
        return len(shas)

    def test_loose_objects_are_gone_after_repack(self):
        self.repack()
        self.assertEqual(self.loose_objects(), [])
        self.assertTrue(any(f.endswith(".pack") for f in os.listdir(self.path(".git/objects/pack"))))

    def test_every_object_readable_from_pack_with_ofs_deltas(self):
        self.repack()
        kinds = self.pack_object_kinds()
        self.assertGreater(kinds.get(6, 0), 5, kinds)
        self.assertGreater(self.all_objects_match_git(), 100)

    def test_every_object_readable_with_ref_deltas(self):
        self.repack(config=["repack.useDeltaBaseOffset=false"])
        kinds = self.pack_object_kinds()
        self.assertGreater(kinds.get(7, 0), 5, kinds)
        self.assertEqual(kinds.get(6, 0), 0)
        self.all_objects_match_git()

    def test_log_and_show_work_on_gc_repository(self):
        self.git("gc", "-q")
        self.assertEqual(self.loose_objects(), [])
        self.assertEqual(self.pgb("log"), self.gitb("log", "--no-decorate"))
        self.assertEqual(self.pgb("show"), self.gitb("show", "--no-decorate"))
        self.assertEqual(self.pgb("show", "HEAD~7"), self.gitb("show", "--no-decorate", "HEAD~7"))
        self.assertEqual(self.pgb("ls-tree", "-r", "HEAD~4"), self.gitb("ls-tree", "-r", "HEAD~4"))
        self.assertEqual(self.pgb("cat-file", "-p", "HEAD~9:big.txt"), self.gitb("cat-file", "-p", "HEAD~9:big.txt"))

    def test_refs_are_read_from_packed_refs(self):
        self.git("pack-refs", "--all")
        self.assertFalse(os.path.exists(self.path(".git/refs/heads/main")))
        self.assertTrue(os.path.exists(self.path(".git/packed-refs")))
        for spec in ("main", "old", "light", "v1", "v1^{commit}", "HEAD", "HEAD~3", "v1^{tree}"):
            self.assertEqual(self.pg("rev-parse", spec), self.git("rev-parse", spec), spec)
        self.assertEqual(self.pg("branch"), self.git("branch"))
        self.assertEqual(self.pg("tag"), self.git("tag"))

    def test_deleting_packed_branch_and_tag(self):
        self.git("pack-refs", "--all")
        self.pg("branch", "-D", "old")
        self.pg("tag", "-d", "light")
        self.assertEqual(self.git("branch", "--list", "old"), "")
        self.assertEqual(self.git("tag", "--list", "light"), "")
        self.assertEqual(self.pg("rev-parse", "v1"), self.git("rev-parse", "v1"))     # annotated tag still fine
        self.fsck()

    def test_short_hash_resolves_inside_pack(self):
        self.git("gc", "-q")
        full = self.git("rev-parse", "HEAD~6").strip()
        self.assertEqual(self.pg("rev-parse", full[:7]).strip(), full)
        self.assertEqual(self.pg("cat-file", "-t", full[:6]).strip(), "commit")

    def test_pygit_can_commit_on_top_of_packed_repository(self):
        self.git("gc", "-q")
        self.write("after-gc.txt", "new\n")
        self.pg("add", "after-gc.txt")
        self.pg_commit("after gc")
        self.write("big.txt", "changed after gc\n")
        self.pg("add", "big.txt")
        self.pg_commit("second after gc")
        self.fsck()
        self.assertEqual(self.git("log", "-2", "--format=%s").split("\n")[:2], ["second after gc", "after gc"])

    def test_checkout_from_packed_repository(self):
        self.git("gc", "-q")
        self.pg("switch", "old")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.read("big.txt"), self.gitb("cat-file", "blob", "old:big.txt"))
        self.pg("switch", "main")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_status_on_packed_repository(self):
        self.git("gc", "-q")
        self.write("big.txt", "x\n")
        self.assert_same_state()

    def test_aggressive_gc_with_deep_chains(self):
        self.git("-c", "gc.aggressiveDepth=100", "-c", "gc.aggressiveWindow=100", "gc", "-q", "--aggressive")
        self.all_objects_match_git()

    def test_idx_fanout_and_prefix_matches(self):
        self.repack()
        pdir = self.path(".git/objects/pack")
        idx = [f for f in os.listdir(pdir) if f.endswith(".idx")][0]
        pack = PackFile(os.path.join(pdir, idx))
        self.addCleanup(pack.close)
        shas = pack.all_shas()
        first = next(shas)
        self.assertEqual(pack.find(bytes.fromhex(first)) is not None, True)
        self.assertEqual(first in pack.prefix_matches(first[:5]), True)
        self.assertIsNone(pack.find(b"\xff" * 20))

    def test_unsupported_idx_version_is_reported(self):
        self.repack()
        pdir = self.path(".git/objects/pack")
        idx = os.path.join(pdir, [f for f in os.listdir(pdir) if f.endswith(".idx")][0])
        os.chmod(idx, 0o644)
        with open(idx, "rb") as f:
            data = bytearray(f.read())
        data[4:8] = struct.pack(">I", 1)
        with open(idx, "wb") as f:
            f.write(data)
        with self.assertRaises(GitError):
            PackFile(idx)

    def test_truncated_pack_gives_error_not_traceback(self):
        self.repack()
        pdir = self.path(".git/objects/pack")
        pack = os.path.join(pdir, [f for f in os.listdir(pdir) if f.endswith(".pack")][0])
        os.chmod(pack, 0o644)
        with open(pack, "r+b") as f:
            f.truncate(200)
        p = self.run_cmd(["python3", "-m", "pygit", "log"], check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn(b"Traceback", p.stderr)
        self.assertIn(b"corrupt pack", p.stderr)
