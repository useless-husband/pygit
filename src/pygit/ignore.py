"""A small implementation of .gitignore matching."""
from __future__ import annotations

import os
import re


def glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """Translate a gitignore glob (already stripped of ``!``, leading and trailing ``/``)."""
    i, n = 0, len(pattern)
    out = []
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern.startswith("**", i):
                before_ok = i == 0 or pattern[i - 1] == "/"
                j = i
                while j < n and pattern[j] == "*":
                    j += 1
                if before_ok and j < n and pattern[j] == "/":
                    out.append("(?:.*/)?")       # "**/" matches zero or more directories
                    i = j + 1
                    continue
                if before_ok and j == n:
                    out.append(".*")             # trailing "/**" matches everything inside
                    i = j
                    continue
                out.append("[^/]*")
                i = j
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                out.append(re.escape(c))
            else:
                body = pattern[i + 1:j]
                if body[:1] in ("!", "^"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j
        elif c == "\\" and i + 1 < n:
            i += 1
            out.append(re.escape(pattern[i]))
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out), re.DOTALL)


class Pattern:
    def __init__(self, text: str, base: str = ""):
        self.negate = text.startswith("!")
        if self.negate:
            text = text[1:]
        self.dir_only = text.endswith("/")
        text = text.rstrip("/") if self.dir_only else text
        self.anchored = "/" in text
        text = text.lstrip("/")
        self.base = base            # "" or "sub/dir/"
        self.regex = glob_to_regex(text)

    def matches(self, rel: str, is_dir: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        if not rel.startswith(self.base):
            return False
        sub = rel[len(self.base):]
        if not self.anchored:
            sub = sub.rsplit("/", 1)[-1]
        return self.regex.fullmatch(sub) is not None


def parse_ignore_lines(text: str, base: str = "") -> list[Pattern]:
    patterns = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        # trailing spaces are ignored unless escaped with a backslash
        stripped = line.rstrip(" ")
        if stripped.endswith("\\") and len(stripped) < len(line):
            stripped += " "
        line = stripped
        if not line or line.startswith("#"):
            continue
        if line in ("!", "/"):
            continue
        patterns.append(Pattern(line, base))
    return patterns


class IgnoreMatcher:
    """Decides whether a path (relative to the work tree root, ``/`` separated) is ignored."""

    def __init__(self, root: str, gitdir: str):
        self.root = root
        self._exclude = self._read(os.path.join(gitdir, "info", "exclude"), "")
        self._dir_patterns: dict[str, list[Pattern]] = {}
        self._cache: dict[tuple[str, bool], bool] = {}

    @staticmethod
    def _read(path: str, base: str) -> list[Pattern]:
        try:
            with open(path, encoding="utf-8", errors="surrogateescape") as f:
                return parse_ignore_lines(f.read(), base)
        except (OSError, UnicodeError):
            return []

    def _patterns_in(self, dirrel: str) -> list[Pattern]:
        if dirrel not in self._dir_patterns:
            base = dirrel + "/" if dirrel else ""
            self._dir_patterns[dirrel] = self._read(os.path.join(self.root, dirrel, ".gitignore"), base)
        return self._dir_patterns[dirrel]

    def _match(self, rel: str, is_dir: bool) -> bool:
        key = (rel, is_dir)
        if key in self._cache:
            return self._cache[key]
        parts = rel.split("/")
        patterns = list(self._exclude)
        for i in range(len(parts)):
            patterns += self._patterns_in("/".join(parts[:i]))
        result = False
        for p in patterns:
            if p.matches(rel, is_dir):
                result = not p.negate
        self._cache[key] = result
        return result

    def is_ignored(self, rel: str, is_dir: bool = False) -> bool:
        parts = rel.split("/")
        for i in range(1, len(parts)):
            if self._match("/".join(parts[:i]), True):
                return True
        return self._match(rel, is_dir)
