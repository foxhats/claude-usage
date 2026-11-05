"""Tests for the btop-style Code mode screen."""

from datetime import datetime

from rich.console import Console

from claude_usage.code_mode.btop import (
    BtopView,
    UtilHistory,
    braille_graph,
    gradient,
    meter,
    resample,
)


def _render(view, width=110, height=32):
    console = Console(width=width, height=height, force_terminal=False, record=True)
    console.print(view)
    return console.export_text()


def test_gradient_endpoints():
    stops = ("#000000", "#808080", "#ffffff")
    assert gradient(stops, 0) == "#000000"
    assert gradient(stops, 0.5) == "#808080"
    assert gradient(stops, 1) == "#ffffff"


def test_meter_width_and_minimum_fill():
    assert meter(50, 20).cell_len == 20
    # any non-zero usage shows at least one cell
    assert meter(0.4, 20).spans[0].style != meter(0, 20).spans[0].style


def test_braille_graph_full_and_empty():
    full = braille_graph([100] * 4, 2, 2, ("#111111", "#222222", "#333333"))
    assert [line.plain for line in full] == ["⣿⣿", "⣿⣿"]
    empty = braille_graph([None] * 4, 2, 2, ("#111111", "#222222", "#333333"))
    assert [line.plain for line in empty] == ["  ", "  "]


def test_resample_leaves_gaps_when_monitor_was_off():
    samples = [(0, 10.0), (100, 20.0)]
    out = resample(samples, now=1000, span=1000, columns=10, max_gap=150)
    assert out[:3] == [20.0, 20.0, 20.0]  # held for up to max_gap + one slot
    assert out[3:] == [None] * 7


def test_history_records_one_sample_per_update(tmp_path):
    h = UtilHistory(tmp_path / "h.db")
    usage = {"five_hour": {"utilization": 5}, "seven_day": {"utilization": 30}}
    t = datetime(2026, 1, 1, 12, 0, 0)
    h.record(usage, t)
    h.record(usage, t)
    assert len(h.samples) == 1


def test_screen_renders_boxes_wide_and_narrow(tmp_path):
    usage = {
        "five_hour": {"utilization": 12, "resets_at": "2099-01-01T00:00:00+00:00"},
        "seven_day": {"utilization": 40, "resets_at": "2099-01-01T00:00:00+00:00"},
        "seven_day_breakdown": {
            "window_started_at": "2026-01-01T00:00:00+00:00",
            "rows": [{"key": "claude_code", "display_name": "Claude Code", "percent": 90}],
        },
    }
    profile = {"account": {"display_name": "pat", "email": "pat@example.com"}}
    view = BtopView(
        usage=usage,
        profile=profile,
        error_message=None,
        last_update=datetime.now(),
        history=UtilHistory(tmp_path / "h.db"),
    )
    for width in (110, 70):
        text = _render(view, width=width)
        lines = text.rstrip("\n").split("\n")
        assert len(lines) == 32
        assert all(len(line) == width for line in lines)
        for title in ("limits", "account", "history", "7-day breakdown", "q quit"):
            assert title in text
