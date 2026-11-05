"""Render docs/screenshot.svg from made-up demo data.

Run from the repo root:   python docs/make_screenshot.py

No real account, token or network call is involved. The history samples are
generated here, and the profile is a placeholder ("Demo User").
"""

import math
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console  # noqa: E402

from claude_usage.code_mode.btop import BtopView, UtilHistory  # noqa: E402


def demo_history(db_path):
    hist = UtilHistory(db_path)
    now = time.time()
    hist.samples = []
    for i in range(0, 20 * 3600, 120):  # 20 hours of 2-minute samples
        hours = i / 3600
        five = 35 + 30 * math.sin(hours / 1.6) + 12 * math.sin(hours * 2.3)
        five = max(0, min(100, five))
        if 7 < hours < 10:
            five = None  # monitor was closed for a while -> gap in the graph
        hist.samples.append((int(now - 20 * 3600 + i), five, 41 + hours * 0.9))
    hist.samples.append((int(now), 63.0, 59.0))
    return hist


def main():
    utc = datetime.now(timezone.utc)
    week_reset = (utc + timedelta(days=2, hours=7)).isoformat()
    usage = {
        "five_hour": {
            "utilization": 63,
            "resets_at": (utc + timedelta(hours=2, minutes=14)).isoformat(),
        },
        "seven_day": {"utilization": 59, "resets_at": week_reset},
        "seven_day_opus": {"utilization": 34, "resets_at": week_reset},
    }
    profile = {
        "account": {
            "display_name": "Demo User",
            "email": "demo@example.com",
            "has_claude_max": True,
        },
        "organization": {"rate_limit_tier": "default_claude_max_5x"},
    }
    with tempfile.TemporaryDirectory() as tmp:
        view = BtopView(
            usage=usage,
            profile=profile,
            error_message=None,
            last_update=datetime.now(),
            history=demo_history(Path(tmp) / "demo.db"),
            next_poll_in=73,
            polling=(120, None),
            extra_boxes=[],
            footer_keys=[("q", "quit")],
        )
        console = Console(
            record=True,
            width=112,
            height=30,
            force_terminal=True,
            color_system="truecolor",
            file=open(Path(tmp) / "out.txt", "w"),
        )
        console.print(view)
        out = ROOT / "docs" / "screenshot.svg"
        console.save_svg(str(out), title="claude-usage")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
