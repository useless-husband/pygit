"""Myers diff and unified-diff output in the same shape as ``git diff``."""
from __future__ import annotations

from .util import quote_path

MAX_EDIT_DISTANCE = 4000   # beyond this, fall back to "replace everything" (still a valid diff)


def myers_ops(a: list, b: list) -> list[str]:
    """Shortest edit script from ``a`` to ``b`` as a list of ``'='``, ``'-'`` and ``'+'``.

    This is the O(ND) algorithm from Eugene Myers' 1986 paper. Common prefix and suffix are
    trimmed first, which is both faster and matches what git does.
    """
    n, m = len(a), len(b)
    p = 0
    while p < n and p < m and a[p] == b[p]:
        p += 1
    s = 0
    while s < n - p and s < m - p and a[n - 1 - s] == b[m - 1 - s]:
        s += 1
    A, B = a[p:n - s], b[p:m - s]
    return ["="] * p + _myers_core(A, B) + ["="] * s


def _myers_core(A: list, B: list) -> list[str]:
    N, M = len(A), len(B)
    if N == 0:
        return ["+"] * M
    if M == 0:
        return ["-"] * N
    v = {1: 0}
    trace = []
    found = False
    for d in range(min(N + M, MAX_EDIT_DISTANCE) + 1):
        trace.append(dict(v))
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v[k - 1] < v[k + 1]):
                x = v[k + 1]              # step down: insert a line of B
            else:
                x = v[k - 1] + 1          # step right: delete a line of A
            y = x - k
            while x < N and y < M and A[x] == B[y]:
                x += 1
                y += 1
            v[k] = x
            if x >= N and y >= M:
                found = True
                break
        if found:
            break
    if not found:
        return ["-"] * N + ["+"] * M
    ops: list[str] = []
    x, y = N, M
    for d in range(len(trace) - 1, -1, -1):
        vd = trace[d]
        k = x - y
        if k == -d or (k != d and vd[k - 1] < vd[k + 1]):
            prev_k = k + 1
        else:
            prev_k = k - 1
        prev_x = vd[prev_k]
        prev_y = prev_x - prev_k
        while x > prev_x and y > prev_y:
            ops.append("=")
            x -= 1
            y -= 1
        if d > 0:
            ops.append("-" if x > prev_x else "+")
        x, y = prev_x, prev_y
    ops.reverse()
    return ops


def split_lines(data: bytes) -> list[bytes]:
    """Split on ``\\n`` only, keeping the newline. The last line may lack one."""
    if not data:
        return []
    lines = data.split(b"\n")
    last = lines.pop()
    out = [ln + b"\n" for ln in lines]
    if last:
        out.append(last)
    return out


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8000]


def _funcname(a: list[bytes], start: int) -> str:
    """git's default hunk header context: the closest earlier line starting with a letter, _ or $."""
    for i in range(start - 1, -1, -1):
        line = a[i]
        c = line[:1]
        if c.isalpha() or c in (b"_", b"$"):
            text = line.rstrip()[:80]
            return text.decode("utf-8", "surrogateescape")
    return ""


def _range(start: int, count: int) -> str:
    if count == 1:
        return f"{start}"
    return f"{start},{count}"


def unified_hunks(a: list[bytes], b: list[bytes], context: int = 3) -> list[str]:
    """Return the ``@@`` hunks (each a string ending in a newline) for two line lists."""
    tags = myers_ops(a, b)
    pos = []
    ai = bi = 0
    for t in tags:
        pos.append((ai, bi))
        if t != "+":
            ai += 1
        if t != "-":
            bi += 1
    changes = [i for i, t in enumerate(tags) if t != "="]
    if not changes:
        return []
    groups = []
    start = end = changes[0]
    for i in changes[1:]:
        if i - end - 1 <= 2 * context:
            end = i
        else:
            groups.append((start, end))
            start = end = i
    groups.append((start, end))
    hunks = []
    for s, e in groups:
        lo, hi = max(0, s - context), min(len(tags), e + context + 1)
        a0, b0 = pos[lo]
        a_count = sum(1 for t in tags[lo:hi] if t != "+")
        b_count = sum(1 for t in tags[lo:hi] if t != "-")
        a_start = a0 + 1 if a_count else a0
        b_start = b0 + 1 if b_count else b0
        head = f"@@ -{_range(a_start, a_count)} +{_range(b_start, b_count)} @@"
        fn = _funcname(a, a0)
        if fn:
            head += " " + fn
        body = [head + "\n"]
        for i in range(lo, hi):
            t = tags[i]
            ai, bi = pos[i]
            line = b[bi] if t == "+" else a[ai]
            prefix = {"=": " ", "-": "-", "+": "+"}[t]
            text = prefix + line.decode("utf-8", "surrogateescape")
            if not text.endswith("\n"):
                text += "\n\\ No newline at end of file\n"
            body.append(text)
        hunks.append("".join(body))
    return hunks


def _name(prefix: str, path: str) -> str:
    return quote_path(prefix + path)


def format_file_diff(path: str, old, new) -> str:
    """Diff one file. ``old`` / ``new`` are ``None`` or ``(mode, sha, data)``."""
    if old and new and (old[0] >> 12) != (new[0] >> 12):     # e.g. file <-> symlink
        return format_file_diff(path, old, None) + format_file_diff(path, None, new)
    if old and new and old[0] == new[0] and old[1] == new[1]:
        return ""
    lines = [f"diff --git {_name('a/', path)} {_name('b/', path)}"]
    zero = "0" * 7
    if old is None:
        lines.append(f"new file mode {new[0]:o}")
        lines.append(f"index {zero}..{new[1][:7]}")
    elif new is None:
        lines.append(f"deleted file mode {old[0]:o}")
        lines.append(f"index {old[1][:7]}..{zero}")
    else:
        if old[0] != new[0]:
            lines.append(f"old mode {old[0]:o}")
            lines.append(f"new mode {new[0]:o}")
        if old[1] == new[1]:
            return "\n".join(lines) + "\n"
        suffix = f" {old[0]:o}" if old[0] == new[0] else ""
        lines.append(f"index {old[1][:7]}..{new[1][:7]}{suffix}")
    old_data = old[2] if old else b""
    new_data = new[2] if new else b""
    a_name = "/dev/null" if old is None else _name("a/", path)
    b_name = "/dev/null" if new is None else _name("b/", path)
    if is_binary(old_data) or is_binary(new_data):
        lines.append(f"Binary files {a_name} and {b_name} differ")
        return "\n".join(lines) + "\n"
    if not old_data and not new_data:
        return "\n".join(lines) + "\n"
    tab = "\t" if " " in path else ""
    lines.append(f"--- {a_name}{tab if old else ''}")
    lines.append(f"+++ {b_name}{tab if new else ''}")
    hunks = unified_hunks(split_lines(old_data), split_lines(new_data))
    return "\n".join(lines) + "\n" + "".join(hunks)
