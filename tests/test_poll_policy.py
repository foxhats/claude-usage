"""Poll policy: 2 min, 4 min after any error, abort on a 429 at 4 min."""

from unittest.mock import MagicMock, patch

import pytest

from claude_usage.code_mode.api import ApiError, ClaudeAPIClient
from claude_usage.code_mode.monitor import CodeMonitor


@pytest.fixture
def monitor(tmp_path):
    with patch("claude_usage.code_mode.monitor.Path.home", return_value=tmp_path):
        m = CodeMonitor(credentials_path=tmp_path / "creds.json")
    m.credentials = {"accessToken": "tok", "expiresAt": 9999999999999}
    m.last_profile = {"account": {}}
    m._refresh_credentials = lambda: None
    m._refresh_from_model = lambda: False
    m.oauth_manager = MagicMock()
    m.oauth_manager.is_token_expired.return_value = False
    m.api_client = MagicMock()
    return m


def _fail(m, status, reason="", body=""):
    m.api_client.last_error = ApiError("https://x/usage", status, reason,
                                       {"retry-after": "60", "set-cookie": "s=1"}, body)
    m.api_client.fetch_usage.return_value = (None, m.api_client.last_error.summary())
    m.fetch_usage()


def _ok(m):
    m.api_client.last_error = None
    m.api_client.fetch_usage.return_value = ({"five_hour": {"utilization": 1}}, None)
    m.fetch_usage()


def test_starts_at_two_minutes(monitor):
    assert monitor.poll_interval == 120


def test_uses_single_attempt_requests(monitor):
    _ok(monitor)
    assert monitor.api_client.fetch_usage.call_args.kwargs == {"strict": True}


def test_first_error_is_shown_and_slows_to_four_minutes(monitor):
    _fail(monitor, 503, "Service Unavailable")
    assert monitor.poll_interval == 240
    assert "HTTP 503" in monitor.error_message
    assert "4 min" in monitor.error_message
    assert monitor.abort_report is None


def test_first_error_429_only_slows(monitor):
    _fail(monitor, 429, "Too Many Requests")
    assert monitor.poll_interval == 240
    assert monitor.abort_report is None


def test_stays_slow_after_recovering(monitor):
    _fail(monitor, 500)
    _ok(monitor)
    assert monitor.poll_interval == 240
    assert monitor.error_message is None


def test_non_429_error_at_four_minutes_keeps_running(monitor):
    _fail(monitor, 500)
    _fail(monitor, 502, "Bad Gateway")
    assert monitor.abort_report is None
    assert "HTTP 502" in monitor.error_message


def test_429_at_four_minutes_aborts_with_full_report(monitor):
    _fail(monitor, 500, "Internal Server Error")
    _ok(monitor)
    _fail(monitor, 429, "Too Many Requests", '{"error": {"type": "rate_limit_error"}}')
    report = monitor.abort_report
    assert report is not None
    assert "HTTP 500" in report and "switched to every 4 min" in report
    assert "1 successful poll(s)" in report
    assert "429 Too Many Requests" in report
    assert "retry-after: 60" in report
    assert "set-cookie: <redacted>" in report
    assert "rate_limit_error" in report


def test_strict_fetch_makes_one_request_and_keeps_details():
    client = ClaudeAPIClient()
    resp = MagicMock(status_code=429, reason="Too Many Requests",
                     headers={"retry-after": "30"}, text="slow down")
    with patch("claude_usage.code_mode.api.requests.get", return_value=resp) as get, \
            patch("claude_usage.code_mode.api.time.sleep") as sleep:
        data, err = client.fetch_usage({"Authorization": "x"}, strict=True)
    assert data is None and "429" in err
    assert get.call_count == 1 and not sleep.called
    assert client.last_error.body == "slow down"
    assert not client.is_in_backoff()
