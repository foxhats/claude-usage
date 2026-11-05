"""Regression tests for bugs fixed in the btop fork (see CHANGELOG 2.19.6+btop.1)."""

import re
from unittest.mock import MagicMock, patch

from claude_usage.code_mode.display import UsageRenderer
from claude_usage.code_mode.monitor import TOKEN_EXPIRED_MESSAGE, CodeMonitor
from tests.viz_regression_helpers import _make_pacemaker_status, _render_to_str

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _lines(pm_status):
    out = _render_to_str(UsageRenderer().render_bottom_section(pm_status, {}))
    return [_ANSI.sub("", line) for line in out.splitlines()]


# ── expired login ────────────────────────────────────────────────────────────


def _expired_monitor(tmp_path):
    with patch("claude_usage.code_mode.monitor.Path.home", return_value=tmp_path):
        m = CodeMonitor(credentials_path=tmp_path / "creds.json")
    m.credentials = {"accessToken": "old", "expiresAt": 1}
    m.last_profile = None
    m._refresh_credentials = lambda: None
    m._refresh_from_model = lambda: False
    m._load_profile_cache = lambda: None
    m.oauth_manager = MagicMock()
    m.oauth_manager.is_token_expired.return_value = True
    m.api_client = MagicMock()
    return m


def test_expired_token_makes_no_requests(tmp_path):
    m = _expired_monitor(tmp_path)
    assert m.fetch_usage() is False
    m.api_client.fetch_profile.assert_not_called()
    m.api_client.fetch_usage.assert_not_called()


def test_expired_token_says_how_to_fix_it(tmp_path):
    m = _expired_monitor(tmp_path)
    m.fetch_usage()
    assert m.error_message == TOKEN_EXPIRED_MESSAGE
    assert "claude" in m.error_message


def test_expired_token_still_uses_profile_cache(tmp_path):
    m = _expired_monitor(tmp_path)
    cached = {"account": {"email": "cached@example.com"}}
    m._load_profile_cache = lambda: cached
    m.fetch_profile()
    assert m.last_profile == cached
    m.api_client.fetch_profile.assert_not_called()


# ── 22-column settings panel must not wrap mid-value ─────────────────────────


def test_short_breakdown_stays_on_one_line():
    status = _make_pacemaker_status()
    status["clean_code_rules_count"] = 17
    status["clean_code_rules_breakdown"] = {"custom": 3, "deleted": 1}
    lines = _lines(status)
    assert any("Rules:" in line and "17 (15 + 3 - 1)" in line for line in lines)


def test_long_breakdown_moves_formula_to_its_own_line():
    status = _make_pacemaker_status()
    status["danger_bash_rules_count"] = 57
    status["danger_bash_rules_breakdown"] = {"custom": 3, "deleted": 1}
    lines = _lines(status)
    i = next(i for i, line in enumerate(lines) if re.match(r"Danger Bash:\s+57\b", line))
    assert lines[i + 1].strip().startswith("(55 + 3 - 1)")


def test_log_large_fits_on_one_line():
    status = _make_pacemaker_status(error_count_24h=-1)
    assert any("Errors 24h:" in line and "log large" in line for line in _lines(status))
