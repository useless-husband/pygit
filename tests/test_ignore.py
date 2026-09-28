import os

import helpers
from pygit.ignore import IgnoreMatcher, glob_to_regex
from pygit.worktree import walk_files


class IgnoreRulesTests(helpers.RepoCase):
    def matcher(self, rules, sub=None, exclude=None):
        os.makedirs(self.path(".git/info"), exist_ok=True)
        self.write(".gitignore", rules)
        for rel, text in (sub or {}).items():
            self.write(rel, text)
        if exclude is not None:
            self.write(".git/info/exclude", exclude)
        return IgnoreMatcher(self.dir, self.path(".git"))

    def test_star_glob(self):
        m = self.matcher("*.log\n")
        self.assertTrue(m.is_ignored("a.log"))
        self.assertTrue(m.is_ignored("deep/er/a.log"))
        self.assertFalse(m.is_ignored("a.txt"))

    def test_comment_and_blank_lines(self):
        m = self.matcher("# comment\n\n*.tmp\n")
        self.assertTrue(m.is_ignored("x.tmp"))
        self.assertFalse(m.is_ignored("# comment"))

    def test_negation(self):
        m = self.matcher("*.log\n!keep.log\n")
        self.assertTrue(m.is_ignored("a.log"))
        self.assertFalse(m.is_ignored("keep.log"))

    def test_last_match_wins(self):
        m = self.matcher("!keep.log\n*.log\n")
        self.assertTrue(m.is_ignored("keep.log"))

    def test_directory_only_pattern(self):
        m = self.matcher("build/\n")
        self.assertTrue(m.is_ignored("build", True))
        self.assertFalse(m.is_ignored("build", False))       # a file named build
        self.assertTrue(m.is_ignored("build/out.o"))
        self.assertTrue(m.is_ignored("src/build/out.o"))

    def test_cannot_reinclude_inside_ignored_directory(self):
        m = self.matcher("build/\n!build/keep.txt\n")
        self.assertTrue(m.is_ignored("build/keep.txt"))

    def test_leading_slash_anchors_to_root(self):
        m = self.matcher("/todo.txt\n")
        self.assertTrue(m.is_ignored("todo.txt"))
        self.assertFalse(m.is_ignored("sub/todo.txt"))

    def test_slash_in_middle_anchors(self):
        m = self.matcher("doc/*.txt\n")
        self.assertTrue(m.is_ignored("doc/a.txt"))
        self.assertFalse(m.is_ignored("doc/sub/a.txt"))
        self.assertFalse(m.is_ignored("x/doc/a.txt"))

    def test_double_star(self):
        m = self.matcher("**/temp\na/**/z\nlogs/**\n")
        self.assertTrue(m.is_ignored("temp"))
        self.assertTrue(m.is_ignored("x/y/temp"))
        self.assertTrue(m.is_ignored("a/z"))
        self.assertTrue(m.is_ignored("a/b/c/z"))
        self.assertTrue(m.is_ignored("logs/x/y.txt"))
        self.assertFalse(m.is_ignored("logs"))

    def test_question_mark_and_class(self):
        m = self.matcher("f?.txt\n[abc].md\n[!x]y.c\n")
        self.assertTrue(m.is_ignored("f1.txt"))
        self.assertFalse(m.is_ignored("f12.txt"))
        self.assertTrue(m.is_ignored("b.md"))
        self.assertFalse(m.is_ignored("d.md"))
        self.assertTrue(m.is_ignored("ay.c"))
        self.assertFalse(m.is_ignored("xy.c"))

    def test_star_does_not_cross_slash(self):
        m = self.matcher("a/*/c\n")
        self.assertTrue(m.is_ignored("a/b/c"))
        self.assertFalse(m.is_ignored("a/b/b/c"))

    def test_escaped_hash_and_bang(self):
        m = self.matcher("\\#file\n\\!bang\n")
        self.assertTrue(m.is_ignored("#file"))
        self.assertTrue(m.is_ignored("!bang"))

    def test_trailing_spaces_ignored(self):
        m = self.matcher("name   \n")
        self.assertTrue(m.is_ignored("name"))

    def test_nested_gitignore_applies_below_its_directory(self):
        m = self.matcher("", sub={"sub/.gitignore": "secret.txt\n!*.keep\n"})
        self.assertTrue(m.is_ignored("sub/secret.txt"))
        self.assertTrue(m.is_ignored("sub/deeper/secret.txt"))
        self.assertFalse(m.is_ignored("secret.txt"))

    def test_nested_gitignore_overrides_parent(self):
        m = self.matcher("*.dat\n", sub={"sub/.gitignore": "!important.dat\n"})
        self.assertTrue(m.is_ignored("a.dat"))
        self.assertFalse(m.is_ignored("sub/important.dat"))

    def test_info_exclude(self):
        m = self.matcher("", exclude="private/\n")
        self.assertTrue(m.is_ignored("private/x"))

    def test_regex_escapes_special_characters(self):
        self.assertIsNotNone(glob_to_regex("a+b(c).txt").fullmatch("a+b(c).txt"))
        self.assertIsNone(glob_to_regex("a.txt").fullmatch("aXtxt"))

    def test_unclosed_bracket_is_literal(self):
        self.assertIsNotNone(glob_to_regex("a[b").fullmatch("a[b"))


class IgnoreAgainstGitTests(helpers.RepoCase):
    def test_walk_matches_git_ls_files_others(self):
        self.init()
        self.write(".gitignore", "*.log\n!keep.log\nbuild/\n/root_only\ndoc/*.txt\n**/tmp\n#c\nsecret*\n[ab].x\n")
        self.write("sub/.gitignore", "local.txt\n!*.log\n")
        files = ["a.log", "keep.log", "build/o.o", "src/build/o.o", "root_only", "sub/root_only",
                 "doc/a.txt", "doc/sub/b.txt", "x/tmp/f", "tmp", "secret1", "sub/secret2", "a.x", "c.x",
                 "sub/local.txt", "sub/z.log", "sub/deep/local.txt", "plain.txt", "中文.log", "空 白.txt"]
        for f in files:
            self.write(f, "x\n")
        expected = self.git("-c", "core.quotepath=false", "ls-files", "-o", "--exclude-standard").splitlines()
        from pygit.util import pkey
        got = walk_files(self.dir, IgnoreMatcher(self.dir, self.path(".git")))
        self.assertEqual(sorted(got, key=pkey), sorted(expected, key=pkey))
