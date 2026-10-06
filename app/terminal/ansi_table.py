"""Render colourised, width-aware tables for xterm.js (pure ANSI)."""

from __future__ import annotations

from typing import Any, Sequence

# ANSI
_RST = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[1;36m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_WHITE = "\033[37m"


def _visible_len(text: str) -> int:
    """Length ignoring ANSI CSI sequences."""
    n = 0
    i = 0
    s = text or ""
    while i < len(s):
        if s[i] == "\033" and i + 1 < len(s) and s[i + 1] == "[":
            j = i + 2
            while j < len(s) and s[j] not in "m":
                j += 1
            i = j + 1
            continue
        n += 1
        i += 1
    return n


def _clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _visible_len(text) <= width:
        return text
    if width <= 1:
        return "…"
    # strip ANSI for clipping then plain truncate
    plain = []
    i = 0
    s = text or ""
    while i < len(s):
        if s[i] == "\033" and i + 1 < len(s) and s[i + 1] == "[":
            j = i + 2
            while j < len(s) and s[j] not in "m":
                j += 1
            i = j + 1
            continue
        plain.append(s[i])
        i += 1
    t = "".join(plain)
    if len(t) <= width:
        return t
    return t[: max(0, width - 1)] + "…"


def _pad(text: str, width: int) -> str:
    vis = _visible_len(text)
    if vis >= width:
        return _clip(text, width)
    return text + (" " * (width - vis))


def _status_style(val: str) -> str:
    low = (val or "").lower()
    if any(k in low for k in ("up ", "running", "healthy", "online")):
        return f"{_GREEN}{val}{_RST}"
    if any(k in low for k in ("exit", "dead", "unhealthy", "stopped")):
        return f"{_RED}{val}{_RST}"
    if any(k in low for k in ("created", "paused", "restarting")):
        return f"{_YELLOW}{val}{_RST}"
    return val


def format_ansi_table(
    title: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    term_cols: int = 120,
) -> str:
    """Build a multi-line ANSI table that fits inside ``term_cols``."""
    cols = [str(c) for c in columns]
    data = [[("" if c is None else str(c)) for c in row] for row in rows]
    n = len(cols)
    if n == 0:
        return f"\r\n{_DIM}(empty table){_RST}\r\n"

    # Preferred relative weights for known docker columns
    weights = []
    for c in cols:
        key = c.lower()
        if key in ("id", "pid"):
            weights.append(1.0)
        elif key in ("tag", "size", "driver", "scope", "mode", "cpu %", "mem %", "age", "created"):
            weights.append(1.2)
        elif key in ("status", "state", "ports", "command", "net i/o", "block i/o", "mem usage"):
            weights.append(2.2)
        elif key in ("name", "names", "image", "repository", "config"):
            weights.append(2.0)
        else:
            weights.append(1.5)

    # Available width: leave margin for separators " │ " between cols
    sep = " │ "
    sep_w = len(sep) * (n - 1) if n > 1 else 0
    usable = max(20, int(term_cols) - 2 - sep_w)
    total_w = sum(weights)
    widths = [max(4, int(usable * w / total_w)) for w in weights]
    # Fix rounding so sum == usable
    while sum(widths) > usable and max(widths) > 4:
        widths[widths.index(max(widths))] -= 1
    while sum(widths) < usable:
        widths[widths.index(min(widths))] += 1

    # Header
    header_cells = [_pad(f"{_CYAN}{_BOLD}{cols[i]}{_RST}", widths[i]) for i in range(n)]
    header_line = sep.join(header_cells)

    # Rule under header (dim box-drawing)
    rule_parts = ["─" * widths[i] for i in range(n)]
    rule_line = f"{_DIM}{sep.join(rule_parts)}{_RST}"

    # Body
    body_lines = []
    status_idx = {i for i, c in enumerate(cols) if c.lower() in ("status", "state")}
    for row in data:
        cells = []
        for i in range(n):
            val = row[i] if i < len(row) else ""
            if i in status_idx:
                val = _status_style(val)
            cells.append(_pad(val, widths[i]))
        body_lines.append(sep.join(cells))

    out = []
    out.append("")
    out.append(f"{_BOLD}{_MAGENTA}╭─ {_RST}{_BOLD}{title}{_RST}{_DIM}  ({len(data)} rows){_RST}")
    out.append(header_line)
    out.append(rule_line)
    if body_lines:
        out.extend(body_lines)
    else:
        out.append(f"{_DIM}(no rows){_RST}")
    out.append(f"{_DIM}╰{'─' * max(8, min(usable, 40))}{_RST}")
    out.append("")
    # Use \r\n so xterm advances cleanly
    return "\r\n".join(out) + "\r\n"
