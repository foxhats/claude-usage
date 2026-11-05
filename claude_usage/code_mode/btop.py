"""btop-inspired full-screen layout for Code mode.

Rounded boxes with titles notched into the border (╭─┐¹title┌──╮), gradient
"■" meters, and a braille history graph, using btop's default color theme.
"""

import sqlite3
import time
from datetime import datetime, timedelta, timezone

from rich.console import Group
from rich.segment import Segment
from rich.style import Style
from rich.text import Text


# ── theme (btop "Default") ────────────────────────────────────────────────────

MAIN_FG = "#cccccc"
TITLE = "#eeeeee"
HI_FG = "#b54040"  # shortcut letters and box numbers
INACTIVE_FG = "#404040"
GRAPH_TEXT = "#606060"
METER_BG = "#404040"
DIV_LINE = "#303030"
ERROR_RED = "#dc4c4c"

LIMITS_BOX = "#556d59"  # btop cpu box
ACCOUNT_BOX = "#6c6c4b"  # btop mem box
HISTORY_BOX = "#5c588d"  # btop net box
BREAKDOWN_BOX = "#805252"  # btop proc box

CPU_GRADIENT = ("#77ca9b", "#cbc06c", "#dc4c4c")
SHARE_GRADIENT = ("#3a7ca5", "#5fb3d9", "#74e6fc")  # btop "cached" hues
WEEK_GRADIENT = ("#4f43a3", "#7d6fd0", "#dcafde")  # btop download/upload blend

SUPERSCRIPT = "⁰¹²³⁴⁵⁶⁷⁸⁹"

GRAPH_SPAN_SECONDS = 24 * 3600


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def gradient(stops, frac):
    """Color at `frac` (0..1) along a start/mid/end gradient, as '#rrggbb'."""
    frac = min(max(frac, 0.0), 1.0)
    if frac <= 0.5:
        a, b, t = stops[0], stops[1], frac * 2
    else:
        a, b, t = stops[1], stops[2], (frac - 0.5) * 2
    ra, ga, ba = _hex_to_rgb(a)
    rb, gb, bb = _hex_to_rgb(b)
    return "#{:02x}{:02x}{:02x}".format(
        round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t)
    )


# ── small building blocks ─────────────────────────────────────────────────────


def meter(pct, width, stops=CPU_GRADIENT):
    """btop meter: a row of ■, each filled cell colored by its position."""
    text = Text(no_wrap=True)
    if width <= 0:
        return text
    pct = min(max(pct or 0, 0), 100)
    filled = round(pct / 100 * width)
    if pct > 0 and filled == 0:
        filled = 1
    for i in range(width):
        if i < filled:
            frac = i / (width - 1) if width > 1 else 1.0
            text.append("■", style=gradient(stops, frac))
        else:
            text.append("■", style=METER_BG)
    return text


def pct_text(pct, stops=CPU_GRADIENT, width=4):
    pct = pct or 0
    return Text(f"{pct:>{width - 1}.0f}%", style=Style(color=gradient(stops, pct / 100), bold=True))


def fmt_duration(seconds):
    seconds = max(int(seconds), 0)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def _parse_reset(resets_at):
    if not resets_at:
        return None
    try:
        dt = datetime.fromisoformat(resets_at)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def kv(label, value, width, value_style=MAIN_FG, label_style=GRAPH_TEXT):
    """'label     value' row padded to width."""
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append(f" {label:<7}", style=label_style)
    if isinstance(value, Text):
        t.append_text(value)
    else:
        t.append(str(value), style=value_style)
    t.truncate(width, overflow="ellipsis", pad=True)
    return t


# ── braille graph ─────────────────────────────────────────────────────────────

# Dot bits for one braille cell, listed bottom → top.
_LEFT_UP = (0x40, 0x04, 0x02, 0x01)
_RIGHT_UP = (0x80, 0x20, 0x10, 0x08)


def _braille(left, right, downward=False):
    """Braille cell with `left`/`right` (0-4) dots filled from the bottom
    (or from the top when downward)."""
    lbits = _LEFT_UP[::-1] if downward else _LEFT_UP
    rbits = _RIGHT_UP[::-1] if downward else _RIGHT_UP
    code = 0
    for i in range(left):
        code |= lbits[i]
    for i in range(right):
        code |= rbits[i]
    return chr(0x2800 + code) if code else " "


def braille_graph(values, width, height, stops, downward=False):
    """Area graph of `values` (0-100 or None), two values per character cell.

    Returns `height` Text lines. Rows are colored by height like btop.
    """
    cols = width * 2
    vals = list(values)[-cols:]
    vals = [None] * (cols - len(vals)) + vals
    total = height * 4
    dots = []
    for v in vals:
        if v is None:
            dots.append(0)
        else:
            d = round(min(max(v, 0), 100) / 100 * total)
            dots.append(max(d, 1))  # always show a sliver for a live sample
    lines = []
    for row in range(height):
        # distance of this row from the graph's baseline, in cells
        level = row if downward else height - 1 - row
        color = gradient(stops, (level + 0.5) / height)
        line = Text(no_wrap=True)
        for c in range(width):
            left = min(max(dots[2 * c] - 4 * level, 0), 4)
            right = min(max(dots[2 * c + 1] - 4 * level, 0), 4)
            line.append(_braille(left, right, downward), style=color)
        lines.append(line)
    return lines


def resample(samples, now, span, columns, max_gap):
    """Bucket (ts, value) samples into `columns` evenly spaced time slots ending
    at `now`. Each slot holds the latest sample at or before its end, if that
    sample is no older than `max_gap`; otherwise None (monitor wasn't running)."""
    out = []
    step = span / columns
    j = -1
    for i in range(columns):
        t_end = now - span + (i + 1) * step
        while j + 1 < len(samples) and samples[j + 1][0] <= t_end:
            j += 1
        if j >= 0 and t_end - samples[j][0] <= max_gap + step:
            out.append(samples[j][1])
        else:
            out.append(None)
    return out


# ── history store ─────────────────────────────────────────────────────────────


class UtilHistory:
    """Utilization samples for the history graph (kept 7 days)."""

    RETENTION = 7 * 24 * 3600

    def __init__(self, db_path):
        self.db_path = str(db_path)
        self.samples = []  # (ts, five_hour, seven_day)
        self._last_key = None
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS btop_util_history ("
                    "ts INTEGER PRIMARY KEY, five_hour REAL, seven_day REAL)"
                )
                cutoff = int(time.time()) - self.RETENTION
                self.samples = conn.execute(
                    "SELECT ts, five_hour, seven_day FROM btop_util_history "
                    "WHERE ts >= ? ORDER BY ts",
                    (cutoff,),
                ).fetchall()
        except sqlite3.Error:
            pass

    def record(self, usage, updated_at):
        """Store one sample per distinct `updated_at` (datetime of the data)."""
        if not usage or updated_at is None:
            return
        five = (usage.get("five_hour") or {}).get("utilization")
        seven = (usage.get("seven_day") or {}).get("utilization")
        if five is None and seven is None:
            return
        ts = int(updated_at.timestamp())
        if ts == self._last_key or (self.samples and ts <= self.samples[-1][0]):
            return
        self._last_key = ts
        self.samples.append((ts, five, seven))
        cutoff = ts - self.RETENTION
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.pop(0)
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO btop_util_history VALUES (?, ?, ?)",
                    (ts, five, seven),
                )
                conn.execute("DELETE FROM btop_util_history WHERE ts < ?", (cutoff,))
        except sqlite3.Error:
            pass

    def series(self, index):
        return [(s[0], s[index]) for s in self.samples if s[index] is not None]


# ── boxes ─────────────────────────────────────────────────────────────────────


class Box:
    """A btop box. `body` is a list of Text lines or any Rich renderable.

    `height` None → fit the body. `top_right` / `bottom_left` / `bottom_right`
    are lists of Text notched into the border as ┐text┌ / ┘text└.
    """

    def __init__(
        self,
        number,
        title,
        color,
        body,
        height=None,
        top_right=None,
        bottom_left=None,
        bottom_right=None,
    ):
        self.number = number
        self.title = title
        self.color = color
        self.body = body
        self.height = height
        self.top_right = top_right or []
        self.bottom_left = bottom_left or []
        self.bottom_right = bottom_right or []

    def _border(self, width, left_corner, right_corner, left_items, right_items, top):
        b = Style(color=self.color)
        open_, close = ("┐", "┌") if top else ("┘", "└")
        line = Text(no_wrap=True)
        line.append(left_corner + "─", style=b)
        for item in left_items:
            line.append(open_, style=b)
            line.append_text(item)
            line.append(close, style=b)
        right = Text(no_wrap=True)
        for item in right_items:
            right.append(open_, style=b)
            right.append_text(item)
            right.append(close, style=b)
        right.append("─" + right_corner, style=b)
        fill = width - line.cell_len - right.cell_len
        if fill < 0:  # too narrow for the right-hand notches
            right = Text("─" + right_corner, style=b)
            fill = width - line.cell_len - right.cell_len
        line.append("─" * max(fill, 0), style=b)
        line.append_text(right)
        line.truncate(width)
        return line

    def title_text(self):
        t = Text()
        if self.number is not None:
            t.append(SUPERSCRIPT[self.number % 10], style=HI_FG)
        t.append(self.title, style=Style(color=TITLE, bold=True))
        return t

    def render_lines(self, console, options, width):
        inner = max(width - 2, 1)
        body = self.body
        if isinstance(body, list):
            body = Group(*body) if body else Text("")
        want = max(self.height - 2, 0) if self.height is not None else None
        opts = options.update(width=inner, height=want)
        rows = console.render_lines(body, opts, pad=True)
        if want is not None:
            blank = [Segment(" " * inner)]
            rows = rows[:want] + [blank] * (want - len(rows))
        b = Style(color=self.color)
        top = self._border(width, "╭", "╮", [self.title_text()], self.top_right, True)
        bottom = self._border(
            width, "╰", "╯", self.bottom_left, self.bottom_right, False
        )
        out = [list(top.render(console))]
        for row in rows:
            out.append([Segment("│", b), *row, Segment("│", b)])
        out.append(list(bottom.render(console)))
        return out


class Columns:
    """Boxes side by side; the last one takes the leftover width."""

    def __init__(self, boxes, widths):
        self.boxes = boxes
        self.widths = widths  # None = flexible

    def render_lines(self, console, options, width):
        fixed = sum(w for w in self.widths if w)
        flex = max(width - fixed, 1)
        rendered = [
            box.render_lines(console, options, w or flex)
            for box, w in zip(self.boxes, self.widths)
        ]
        height = max(len(r) for r in rendered)
        out = []
        for i in range(height):
            row = []
            for r, box, w in zip(rendered, self.boxes, self.widths):
                row.extend(r[i] if i < len(r) else [Segment(" " * (w or flex))])
            out.append(row)
        return out


class Screen:
    """Stack of boxes / Columns filling the terminal; one may be elastic."""

    def __init__(self, rows, elastic=None, min_elastic=6):
        self.rows = rows
        self.elastic = elastic  # a Box whose height absorbs leftover space
        self.min_elastic = min_elastic

    def __rich_console__(self, console, options):
        width = options.max_width
        height = options.height or console.size.height
        if self.elastic is not None:
            used = 0
            for row in self.rows:
                if row is not self.elastic:
                    used += len(row.render_lines(console, options, width))
            self.elastic.height = max(height - used, self.min_elastic)
        lines = []
        for row in self.rows:
            lines.extend(row.render_lines(console, options, width))
        if options.height:
            lines = lines[: options.height]
        for line in lines:
            yield from line
            yield Segment.line()


# ── screen assembly ───────────────────────────────────────────────────────────

_TIER_NAMES = {
    "default_claude_max_5x": "max 5x",
    "default_claude_max_20x": "max 20x",
    "default_claude_ai": "standard",
}

_LIMITS = (
    ("five_hour", "5-hour", 5 * 3600),
    ("seven_day", "7-day", 7 * 86400),
    ("seven_day_sonnet", "sonnet", 7 * 86400),
    ("seven_day_opus", "opus", 7 * 86400),
)


def _limit_lines(usage, width, now):
    lines = []
    label_w, pct_w, gap = 9, 5, 1
    meter_w = max(width - label_w - pct_w - gap - 1, 4)
    for key, label, window in _LIMITS:
        data = usage.get(key) if usage else None
        if not data:
            continue
        util = data.get("utilization") or 0
        line = Text(no_wrap=True)
        line.append(f" {label:<{label_w - 1}}", style=Style(color=TITLE, bold=True))
        line.append_text(meter(util, meter_w))
        line.append(" ")
        line.append_text(pct_text(util, width=pct_w))
        lines.append(line)

        reset = _parse_reset(data.get("resets_at"))
        sub = Text(no_wrap=True, overflow="ellipsis")
        sub.append(" " * label_w)
        if reset:
            left = (reset - now).total_seconds()
            if left > 0:
                elapsed = 100 * (1 - left / window)
                sub.append("↻ ", style=HI_FG)
                sub.append(f"resets in {fmt_duration(left)}", style=MAIN_FG)
                sub.append(f"  ·  {max(elapsed, 0):.0f}% of window elapsed", style=GRAPH_TEXT)
            else:
                sub.append("↻ window expired", style=GRAPH_TEXT)
        lines.append(sub)
    if not lines:
        lines.append(Text(" waiting for usage data…", style=GRAPH_TEXT))
    return lines


def _account_lines(profile, error_message, last_update, next_poll_in, width, polling=None):
    account = (profile or {}).get("account", {})
    org = (profile or {}).get("organization", {})
    lines = []
    name = (account.get("display_name") or "").replace("Organization", "").strip()
    if name:
        lines.append(kv("user", Text(name, style=Style(color=TITLE, bold=True)), width))
    if account.get("email"):
        lines.append(kv("email", account["email"], width))

    plan = Text()
    if org.get("organization_type") == "claude_enterprise":
        plan.append("ENTERPRISE ", style=Style(color="#5474e8", bold=True))
    if account.get("has_claude_max"):
        plan.append("MAX ", style=Style(color="#ffb814", bold=True))
    if account.get("has_claude_pro"):
        plan.append("PRO ", style=Style(color="#d9626d", bold=True))
    tier = org.get("rate_limit_tier") or ""
    tier = _TIER_NAMES.get(tier, tier)
    if tier:
        plan.append(tier, style=GRAPH_TEXT)
    if plan:
        lines.append(kv("plan", plan, width))

    if last_update:
        lines.append(kv("synced", last_update.strftime("%H:%M:%S"), width))
    if polling is not None:
        interval, slowed_at = polling
        poll = Text(f"every {interval // 60}m", style=MAIN_FG)
        if slowed_at is not None:
            poll.append(f" · slowed {slowed_at:%H:%M}", style="#cbc06c")
        lines.append(kv("poll", poll, width))
    if next_poll_in is not None:
        mins, secs = divmod(int(next_poll_in), 60)
        lines.append(kv("next", f"{mins}m {secs:02d}s" if mins else f"{secs}s", width))

    status = Text()
    if error_message:
        status.append("● ", style="#dc4c4c")
        status.append(str(error_message), style="#dc4c4c")
    elif profile or last_update:
        status.append("● ", style="#77ca9b")
        status.append("live", style=MAIN_FG)
    else:
        status.append("● ", style="#cbc06c")
        status.append("connecting…", style=MAIN_FG)
    lines.append(kv("status", status, width))
    return lines


def _breakdown_lines(breakdown, width):
    rows = breakdown.get("rows") or []
    name_w = min(max([len(r.get("display_name", "")) for r in rows] + [10]) + 2, 24)
    meter_w = max(width - name_w - 7, 4)
    header = Text(no_wrap=True)
    header.append(f" {'Source':<{name_w - 1}}", style=Style(color=TITLE, bold=True))
    header.append(" " * meter_w)
    header.append("Share", style=Style(color=TITLE, bold=True))
    lines = [header]
    for r in rows:
        pct = r.get("percent") or 0
        line = Text(no_wrap=True)
        line.append(f" {r.get('display_name', r.get('key', '?')):<{name_w - 1}}"[:name_w], style=MAIN_FG)
        line.append_text(meter(pct, meter_w, SHARE_GRADIENT))
        line.append(" ")
        line.append(f"{pct:>4.0f}%", style=Style(color=MAIN_FG, bold=True))
        lines.append(line)
    return lines


class HistoryBody:
    """Mirrored braille graph: 5-hour grows up, 7-day hangs down (btop net box)."""

    def __init__(self, history, now_ts):
        self.history = history
        self.now_ts = now_ts

    def __rich_console__(self, console, options):
        width = options.max_width
        height = options.height or 8
        up_h = height // 2
        down_h = height - up_h - 1  # one row for the baseline
        max_gap = 15 * 60
        five = resample(self.history.series(1), self.now_ts, GRAPH_SPAN_SECONDS, width * 2, max_gap)
        seven = resample(self.history.series(2), self.now_ts, GRAPH_SPAN_SECONDS, width * 2, max_gap)
        up = braille_graph(five, width, up_h, CPU_GRADIENT)
        down = braille_graph(seven, width, down_h, WEEK_GRADIENT, downward=True) if down_h else []

        def label(line, text):
            overlay = Text(no_wrap=True)
            overlay.append_text(text)
            rest = line[overlay.cell_len :] if overlay.cell_len < width else Text()
            overlay.append_text(rest)
            return overlay

        latest = self.history.samples[-1] if self.history.samples else None
        five_now = Text()
        five_now.append(" ▲ ", style=Style(color=CPU_GRADIENT[0], bold=True))
        five_now.append("5-hour ", style=Style(color=TITLE, bold=True))
        if latest and latest[1] is not None:
            five_now.append_text(pct_text(latest[1]))
        five_now.append(" ")
        up[0] = label(up[0], five_now)
        if down:
            seven_now = Text()
            seven_now.append(" ▼ ", style=Style(color=WEEK_GRADIENT[2], bold=True))
            seven_now.append("7-day  ", style=Style(color=TITLE, bold=True))
            if latest and latest[2] is not None:
                seven_now.append_text(pct_text(latest[2], WEEK_GRADIENT))
            seven_now.append(" ")
            down[-1] = label(down[-1], seven_now)

        if len(self.history.samples) < 2:
            msg = "collecting history · new sample on every refresh"
            mid = up_h - 1 if up_h > 1 else 0
            pad = max((width - len(msg)) // 2, 0)
            up[mid] = Text(" " * pad + msg, style=GRAPH_TEXT, no_wrap=True)

        baseline = Text("─" * width, style=DIV_LINE, no_wrap=True)
        yield from Group(*up, baseline, *down).__rich_console__(console, options)


def build_screen(
    width,
    usage,
    profile,
    error_message,
    last_update,
    history,
    next_poll_in=None,
    polling=None,
    extra_boxes=None,
    footer_keys=None,
):
    """Assemble the full btop-style screen renderable."""
    now = datetime.now(timezone.utc)
    clock = Text(datetime.now().strftime("%H:%M:%S"), style=Style(color=TITLE, bold=True))

    wide = width >= 96
    acct_w = 38 if wide else width
    limits_w = (width - acct_w) if wide else width

    limits = Box(
        1, "limits", LIMITS_BOX, _limit_lines(usage, limits_w - 2, now), top_right=[clock]
    )
    account = Box(
        2,
        "account",
        ACCOUNT_BOX,
        _account_lines(
            profile, error_message, last_update, next_poll_in, acct_w - 2, polling
        ),
    )
    if wide:
        n = max(len(limits.body), len(account.body)) + 2
        limits.height = account.height = n
        top_rows = [Columns([limits, account], [None, acct_w])]
    else:
        top_rows = [limits, account]

    span_label = Text("24h", style=Style(color=TITLE, bold=True))
    graph = Box(
        3,
        "history",
        HISTORY_BOX,
        HistoryBody(history, now.timestamp()),
        top_right=[span_label],
        bottom_left=[Text("-24h", style=GRAPH_TEXT)],
        bottom_right=[Text("now", style=GRAPH_TEXT)],
    )

    rows = list(top_rows)
    if error_message:
        msg = Text()
        msg.append(" ● ", style=Style(color=ERROR_RED, bold=True))
        msg.append(str(error_message), style=Style(color=TITLE))
        rows.append(Box(None, "error", ERROR_RED, [msg]))
    rows.append(graph)
    breakdown = (usage or {}).get("seven_day_breakdown")
    if breakdown and breakdown.get("rows"):
        since = _parse_reset(breakdown.get("window_started_at"))
        tr = []
        if since:
            tr.append(Text("since " + since.astimezone().strftime("%a %d %b"), style=GRAPH_TEXT))
        rows.append(
            Box(4, "7-day breakdown", BREAKDOWN_BOX, _breakdown_lines(breakdown, width - 2), top_right=tr)
        )
    rows.extend(extra_boxes or [])

    # shortcut hints notched into the very last box's bottom border
    keys = []
    for key, desc in footer_keys or [("q", "quit")]:
        t = Text()
        t.append(key, style=Style(color=HI_FG, bold=True))
        t.append(" " + desc, style=MAIN_FG)
        keys.append(t)
    last = rows[-1]
    target = last.boxes[0] if isinstance(last, Columns) else last
    target.bottom_left = keys + target.bottom_left

    return Screen(rows, elastic=graph)


class BtopView:
    """Lays the screen out at render time, using the width Rich gives us."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __rich_console__(self, console, options):
        screen = build_screen(options.max_width, **self.kwargs)
        yield from screen.__rich_console__(console, options)
