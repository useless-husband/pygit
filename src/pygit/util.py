"""Shared helpers: errors, path quoting, dates and config files."""
from __future__ import annotations

import calendar
import datetime
import os
import re
import time


class GitError(Exception):
    """A user-facing error. ``code`` is the process exit status."""

    def __init__(self, message: str, code: int = 128):
        super().__init__(message)
        self.message = message
        self.code = code


def pkey(path: str) -> bytes:
    """Sort key that orders paths by their raw bytes, like git does."""
    return path.encode("utf-8", "surrogateescape")


_ESCAPES = {7: "\\a", 8: "\\b", 9: "\\t", 10: "\\n", 11: "\\v", 12: "\\f", 13: "\\r",
            0x22: '\\"', 0x5C: "\\\\"}


def quote_path(path: str, quote_space: bool = False) -> str:
    """Quote a path the way git does with core.quotepath=true."""
    need = False
    out = []
    for c in pkey(path):
        if c in _ESCAPES:
            out.append(_ESCAPES[c])
            need = True
        elif c < 0x20 or c >= 0x7F:
            out.append("\\%03o" % c)
            need = True
        else:
            out.append(chr(c))
            if c == 0x20 and quote_space:
                need = True
    return '"' + "".join(out) + '"' if need else path


# ---------------------------------------------------------------- dates

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def tz_to_minutes(tz: str) -> int:
    sign = -1 if tz.startswith("-") else 1
    return sign * (int(tz[1:3]) * 60 + int(tz[3:5]))


def minutes_to_tz(minutes: int) -> str:
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return f"{sign}{minutes // 60:02d}{minutes % 60:02d}"


def local_tz() -> str:
    return minutes_to_tz((time.localtime().tm_gmtoff or 0) // 60)


def format_date(ts: int, tz: str) -> str:
    """Format like ``git log``: ``Thu Apr 7 22:13:13 2005 +0200``."""
    tzinfo = datetime.timezone(datetime.timedelta(minutes=tz_to_minutes(tz)))
    d = datetime.datetime.fromtimestamp(ts, tzinfo)
    return f"{DAYS[d.weekday()]} {MONTHS[d.month - 1]} {d.day} {d:%H:%M:%S} {d.year} {tz}"


_RAW = re.compile(r"^@?(\d+)(?:\s+([+-]\d{4}))?$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\s*(Z|[+-]\d{2}:?\d{2}))?$")


def parse_date(text: str) -> tuple[int, str]:
    """Parse GIT_AUTHOR_DATE style values: ``@123 +0800``, ``123 +0800`` or ISO 8601."""
    text = text.strip()
    m = _RAW.match(text)
    if m:
        return int(m.group(1)), m.group(2) or "+0000"
    m = _ISO.match(text)
    if m:
        y, mo, d, h, mi, s = (int(x) for x in m.groups()[:6])
        tz = m.group(7)
        if tz is None:
            tz = local_tz()
        elif tz == "Z":
            tz = "+0000"
        else:
            tz = tz.replace(":", "")
        return calendar.timegm((y, mo, d, h, mi, s)) - tz_to_minutes(tz) * 60, tz
    raise GitError(f"invalid date format: {text}")


# --------------------------------------------------------------- config

_SECTION = re.compile(r'^\s*\[\s*([A-Za-z0-9.-]+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*\]\s*(?:[#;].*)?$')
_KEY = re.compile(r"^([A-Za-z][A-Za-z0-9-]*)\s*(?:=\s*(.*))?$")


def _unquote(raw: str) -> str:
    out = []
    in_quote = False
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            out.append({"n": "\n", "t": "\t", "b": "\b"}.get(raw[i + 1], raw[i + 1]))
            i += 2
            continue
        if c == '"':
            in_quote = not in_quote
        elif c in "#;" and not in_quote:
            break
        else:
            out.append(c)
        i += 1
    text = "".join(out)
    return text if in_quote else text.rstrip()


def _config_key(key: str) -> tuple[str, str | None, str]:
    parts = key.split(".")
    if len(parts) < 2:
        raise GitError(f"key does not contain a section: {key}", 1)
    sub = ".".join(parts[1:-1]) or None
    return parts[0].lower(), sub, parts[-1].lower()


class Config:
    """A tiny reader for git's INI-like config files (later files win)."""

    def __init__(self, paths=()):
        self.values: dict[str, str] = {}
        for p in paths:
            self.load(p)

    def load(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return
        section = sub = None
        for line in text.splitlines():
            s = line.strip()
            if not s or s[0] in "#;":
                continue
            m = _SECTION.match(line)
            if m:
                section = m.group(1).lower()
                sub = re.sub(r"\\(.)", r"\1", m.group(2)) if m.group(2) is not None else None
                continue
            m = _KEY.match(s)
            if section is None or not m:
                continue
            value = "true" if m.group(2) is None else _unquote(m.group(2))
            key = section + ("." + sub if sub is not None else "") + "." + m.group(1).lower()
            self.values[key] = value

    def get(self, key: str, default=None):
        section, sub, name = _config_key(key)
        return self.values.get(section + ("." + sub if sub is not None else "") + "." + name, default)


def _quote_value(value: str) -> str:
    if value != value.strip() or any(c in value for c in '#;"\\\n'):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    return value


def set_config(path: str, key: str, value: str) -> None:
    """Set ``section.name`` (or ``section.sub.name``) in a config file."""
    section, sub, name = _config_key(key)
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    new_line = f"\t{name} = {_quote_value(value)}"
    start = None
    for i, line in enumerate(lines):
        m = _SECTION.match(line)
        if m and m.group(1).lower() == section and (m.group(2) or None) == sub:
            start = i
            break
    if start is None:
        header = f'[{section} "{sub}"]' if sub is not None else f"[{section}]"
        lines += [header, new_line]
    else:
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if _SECTION.match(lines[j]):
                end = j
                break
        for j in range(start + 1, end):
            m = _KEY.match(lines[j].strip())
            if m and m.group(1).lower() == name:
                lines[j] = new_line
                break
        else:
            insert_at = end
            while insert_at > start + 1 and not lines[insert_at - 1].strip():
                insert_at -= 1
            lines.insert(insert_at, new_line)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def global_config_path() -> str:
    return os.environ.get("GIT_CONFIG_GLOBAL") or os.path.join(os.path.expanduser("~"), ".gitconfig")
