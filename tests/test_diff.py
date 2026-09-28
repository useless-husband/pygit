import random
import unittest

import helpers  # noqa: F401  (adds src/ to sys.path)
from pygit.diff import format_file_diff, is_binary, myers_ops, split_lines, unified_hunks
from pygit.objects import hash_bytes


def lcs_len(a, b):
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def apply_ops(a, b, ops):
    out, i, j = [], 0, 0
    for t in ops:
        if t == "=":
            assert a[i] == b[j]
            out.append(a[i])
            i += 1
            j += 1
        elif t == "-":
            i += 1
        else:
            out.append(b[j])
            j += 1
    assert i == len(a) and j == len(b)
    return out


def lines(*names):
    return [n.encode() + b"\n" for n in names]


class MyersTests(unittest.TestCase):
    def test_identical(self):
        self.assertEqual(myers_ops(list("abc"), list("abc")), ["="] * 3)

    def test_both_empty(self):
        self.assertEqual(myers_ops([], []), [])

    def test_insert_into_empty(self):
        self.assertEqual(myers_ops([], list("ab")), ["+", "+"])

    def test_delete_everything(self):
        self.assertEqual(myers_ops(list("ab"), []), ["-", "-"])

    def test_replace_puts_deletion_first(self):
        self.assertEqual(myers_ops(["x"], ["y"]), ["-", "+"])

    def test_insert_in_middle(self):
        self.assertEqual(myers_ops(list("ac"), list("abc")), ["=", "+", "="])

    def test_delete_in_middle(self):
        self.assertEqual(myers_ops(list("abc"), list("ac")), ["=", "-", "="])

    def test_paper_example_is_minimal(self):
        ops = myers_ops(list("ABCABBA"), list("CBABAC"))
        self.assertEqual(sum(1 for t in ops if t != "="), 5)
        self.assertEqual(apply_ops(list("ABCABBA"), list("CBABAC"), ops), list("CBABAC"))

    def test_random_inputs_are_minimal_and_reconstruct(self):
        rng = random.Random(1234)
        for _ in range(300):
            a = [rng.choice("abcd") for _ in range(rng.randint(0, 14))]
            b = [rng.choice("abcd") for _ in range(rng.randint(0, 14))]
            ops = myers_ops(a, b)
            self.assertEqual(apply_ops(a, b, ops), b)
            self.assertEqual(sum(1 for t in ops if t != "="), len(a) + len(b) - 2 * lcs_len(a, b))

    def test_large_disjoint_inputs_still_valid(self):
        a = [str(i) for i in range(3000)]
        b = [str(i) + "x" for i in range(3000)]
        self.assertEqual(apply_ops(a, b, myers_ops(a, b)), b)


class SplitLinesTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(split_lines(b""), [])

    def test_trailing_newline(self):
        self.assertEqual(split_lines(b"a\nb\n"), [b"a\n", b"b\n"])

    def test_no_trailing_newline(self):
        self.assertEqual(split_lines(b"a\nb"), [b"a\n", b"b"])

    def test_only_newline_characters_split(self):
        self.assertEqual(split_lines(b"a\r\nb\rc\x0bd\n"), [b"a\r\n", b"b\rc\x0bd\n"])

    def test_binary_detection(self):
        self.assertTrue(is_binary(b"abc\0def"))
        self.assertFalse(is_binary("中文\n".encode()))


class HunkTests(unittest.TestCase):
    def test_simple_replace(self):
        h = unified_hunks(lines("a", "b", "c"), lines("a", "B", "c"))
        self.assertEqual(h, ["@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n"])

    def test_single_line_header_omits_count(self):
        self.assertEqual(unified_hunks(lines("x"), lines("y")), ["@@ -1 +1 @@\n-x\n+y\n"])

    def test_insert_at_start(self):
        self.assertEqual(unified_hunks(lines("x"), lines("n", "x")), ["@@ -1 +1,2 @@\n+n\n x\n"])

    def test_no_changes(self):
        self.assertEqual(unified_hunks(lines("a"), lines("a")), [])

    def test_missing_newline_marker(self):
        h = unified_hunks([b"a"], [b"a\n"])
        self.assertEqual(h, ["@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+a\n"])

    def test_function_context_in_header(self):
        a = lines("def foo", "  x", "  y", "  z", "  w", "  v", "  u")
        b = lines("def foo", "  x", "  y", "  z", "  w", "  v", "  U")
        self.assertEqual(unified_hunks(a, b)[0].split("\n")[0], "@@ -4,4 +4,4 @@ def foo")

    def test_changes_six_lines_apart_merge(self):
        a = lines(*[f"l{i}" for i in range(1, 21)])
        b = list(a)
        b[4], b[11] = b"X\n", b"Y\n"          # lines 5 and 12: 6 unchanged lines between
        self.assertEqual(len(unified_hunks(a, b)), 1)

    def test_changes_seven_lines_apart_split(self):
        a = lines(*[f"l{i}" for i in range(1, 21)])
        b = list(a)
        b[4], b[12] = b"X\n", b"Y\n"
        self.assertEqual(len(unified_hunks(a, b)), 2)

    def test_context_is_three_lines(self):
        a = lines(*[f"l{i}" for i in range(1, 21)])
        b = list(a)
        b[9] = b"X\n"
        h = unified_hunks(a, b)[0]
        self.assertTrue(h.startswith("@@ -7,7 +7,7 @@"))


class FileDiffTests(unittest.TestCase):
    def test_new_file(self):
        data = b"a\nb\n"
        sha = hash_bytes("blob", data)
        self.assertEqual(
            format_file_diff("f.txt", None, (0o100644, sha, data)),
            f"diff --git a/f.txt b/f.txt\nnew file mode 100644\nindex 0000000..{sha[:7]}\n"
            "--- /dev/null\n+++ b/f.txt\n@@ -0,0 +1,2 @@\n+a\n+b\n")

    def test_deleted_file(self):
        data = b"a\n"
        sha = hash_bytes("blob", data)
        self.assertEqual(
            format_file_diff("f", (0o100644, sha, data), None),
            f"diff --git a/f b/f\ndeleted file mode 100644\nindex {sha[:7]}..0000000\n"
            "--- a/f\n+++ /dev/null\n@@ -1 +0,0 @@\n-a\n")

    def test_mode_change_only(self):
        sha = hash_bytes("blob", b"x\n")
        self.assertEqual(format_file_diff("s.sh", (0o100644, sha, b"x\n"), (0o100755, sha, b"x\n")),
                         "diff --git a/s.sh b/s.sh\nold mode 100644\nnew mode 100755\n")

    def test_identical_gives_nothing(self):
        sha = hash_bytes("blob", b"x\n")
        self.assertEqual(format_file_diff("f", (0o100644, sha, b"x\n"), (0o100644, sha, b"x\n")), "")

    def test_binary(self):
        old, new = b"\0a", b"\0b"
        text = format_file_diff("b.bin", (0o100644, hash_bytes("blob", old), old),
                                (0o100644, hash_bytes("blob", new), new))
        self.assertTrue(text.endswith("Binary files a/b.bin and b/b.bin differ\n"))

    def test_empty_new_file_has_no_hunks(self):
        sha = hash_bytes("blob", b"")
        text = format_file_diff("e", None, (0o100644, sha, b""))
        self.assertEqual(text, f"diff --git a/e b/e\nnew file mode 100644\nindex 0000000..{sha[:7]}\n")

    def test_type_change_becomes_delete_plus_create(self):
        a = (0o100644, hash_bytes("blob", b"x\n"), b"x\n")
        b = (0o120000, hash_bytes("blob", b"x"), b"x")
        text = format_file_diff("l", a, b)
        self.assertIn("deleted file mode 100644", text)
        self.assertIn("new file mode 120000", text)

    def test_path_with_space_gets_trailing_tab(self):
        a = (0o100644, hash_bytes("blob", b"x\n"), b"x\n")
        b = (0o100644, hash_bytes("blob", b"y\n"), b"y\n")
        self.assertIn("--- a/a b.txt\t\n+++ b/a b.txt\t\n", format_file_diff("a b.txt", a, b))


if __name__ == "__main__":
    unittest.main()
