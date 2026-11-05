"""API client for Claude Code usage monitoring"""

import random
import time
from datetime import datetime

import requests


class ApiError:
    """Everything known about one failed API request, for error reports."""

    def __init__(self, url, status=None, reason="", headers=None, body="",
                 exception=None, elapsed=None):
        self.url = url
        self.when = datetime.now()
        self.status = status
        self.reason = reason or ""
        self.headers = dict(headers or {})
        self.body = body or ""
        self.exception = exception
        self.elapsed = elapsed

    def summary(self):
        if self.status is None:
            return f"Network error: {self.exception}"
        text = f"HTTP {self.status} {self.reason}".strip()
        if self.status == 401:
            text += " (token expired, run 'claude' to refresh)"
        return text



class ClaudeAPIClient:
    """Client for Claude Code API endpoints"""

    API_BASE = "https://api.anthropic.com"
    USAGE_ENDPOINT = "/api/oauth/usage"
    PROFILE_ENDPOINT = "/api/oauth/profile"

    # Backoff formula: min(300 * 2^consecutive_429s, 3600) — same as pace-maker
    _BACKOFF_BASE = 300
    _BACKOFF_MAX = 3600

    def __init__(self):
        self._consecutive_429s = 0
        self._backoff_until = 0.0
        self.last_error = None  # ApiError from the last strict fetch_usage

    def is_in_backoff(self) -> bool:
        """Return True if currently in exponential backoff period."""
        return time.time() < self._backoff_until

    def _record_429(self):
        """Record a 429 failure and compute next backoff duration.

        Formula: min(300 * 2^consecutive_429s, 3600) — identical to pace-maker.
        consecutive_429s is read BEFORE incrementing so first backoff is 300s (5min).
        """
        duration = min(
            self._BACKOFF_BASE * (2**self._consecutive_429s), self._BACKOFF_MAX
        )
        self._backoff_until = time.time() + duration
        self._consecutive_429s += 1

    def _record_success(self):
        """Reset backoff state after a successful API call."""
        self._consecutive_429s = 0
        self._backoff_until = 0.0

    def get_backoff_remaining_seconds(self) -> float:
        """Return seconds remaining in current backoff, or 0 if not in backoff."""
        remaining = self._backoff_until - time.time()
        return max(remaining, 0.0)

    def fetch_usage(self, auth_headers, strict=False):
        """Fetch current usage data from Claude Code API

        strict=True makes exactly one request: no in-call 429 retries and no
        backoff bookkeeping. On failure `last_error` holds the full details.
        The caller decides what to do about errors.
        """
        if not auth_headers:
            return None, "No authentication headers"
        if strict:
            return self._fetch_usage_once(auth_headers)

        if self.is_in_backoff():
            remaining = self.get_backoff_remaining_seconds()
            return None, f"API backoff: {remaining:.0f}s remaining"

        url = f"{self.API_BASE}{self.USAGE_ENDPOINT}"

        for attempt in range(3):
            try:
                response = requests.get(url, headers=auth_headers, timeout=10)

                if response.status_code == 200:
                    self._record_success()
                    return response.json(), None
                elif response.status_code == 429:
                    if attempt < 2:
                        delay = 4 * (2**attempt) + random.uniform(0, 2)
                        time.sleep(delay)
                        continue
                    # Last attempt exhausted — record persistent backoff
                    self._record_429()
                    return None, "API rate limited (429) after retries"
                elif response.status_code == 401:
                    return None, "Token expired. Run 'claude' to refresh."
                else:
                    return None, f"API error: {response.status_code}"

            except requests.exceptions.RequestException as e:
                return None, f"Network error: {e}"

        return None, "API rate limited (429) after retries"

    def _fetch_usage_once(self, auth_headers):
        url = f"{self.API_BASE}{self.USAGE_ENDPOINT}"
        started = time.time()
        try:
            response = requests.get(url, headers=auth_headers, timeout=10)
        except requests.exceptions.RequestException as e:
            self.last_error = ApiError(
                url, exception=repr(e), elapsed=time.time() - started
            )
            return None, self.last_error.summary()
        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError as e:
                self.last_error = ApiError(
                    url, 200, response.reason, response.headers, response.text,
                    exception=f"invalid JSON: {e}", elapsed=time.time() - started,
                )
                return None, "API returned invalid JSON"
            self.last_error = None
            return data, None
        self.last_error = ApiError(
            url,
            response.status_code,
            response.reason,
            response.headers,
            response.text,
            elapsed=time.time() - started,
        )
        return None, self.last_error.summary()

    def fetch_profile(self, auth_headers):
        """Fetch profile data from Claude Code API"""
        if not auth_headers:
            return None, None, None, "No authentication headers"

        if self.is_in_backoff():
            remaining = self.get_backoff_remaining_seconds()
            return None, None, None, f"API backoff: {remaining:.0f}s remaining"

        url = f"{self.API_BASE}{self.PROFILE_ENDPOINT}"

        for attempt in range(3):
            try:
                response = requests.get(url, headers=auth_headers, timeout=10)

                if response.status_code == 200:
                    profile_data = response.json()

                    # Extract org and account UUIDs
                    org_uuid = None
                    account_uuid = None
                    if profile_data:
                        org = profile_data.get("organization", {})
                        account = profile_data.get("account", {})
                        org_uuid = org.get("uuid")
                        account_uuid = account.get("uuid")

                    self._record_success()
                    return profile_data, org_uuid, account_uuid, None
                elif response.status_code == 429:
                    if attempt < 2:
                        delay = 4 * (2**attempt) + random.uniform(0, 2)
                        time.sleep(delay)
                        continue
                    self._record_429()
                    return None, None, None, "API rate limited (429) after retries"
                else:
                    return None, None, None, f"API error: {response.status_code}"

            except requests.exceptions.RequestException as e:
                return None, None, None, f"Network error: {e}"

        return None, None, None, "API rate limited (429) after retries"
