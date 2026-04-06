"""
src/safeguards/rate_limiter.py

Tracks hourly Claude API call counts, detects rate-limit signals from subprocess
output, and blocks/waits when the limit is reached.

State persisted to: .artifacts/rate_limiter_state.json
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

from config import HOURLY_CALL_LIMIT, RATE_LIMIT_COOLDOWN_SECONDS, RATE_LIMITER_STATE_FILE, AUTONOMOUS_MODE

logger = logging.getLogger(__name__)

# Local aliases to preserve internal references unchanged
STATE_FILE = RATE_LIMITER_STATE_FILE
COOLDOWN_SECONDS = RATE_LIMIT_COOLDOWN_SECONDS

_DIAGNOSTIC_FILE = os.path.join(os.path.dirname(STATE_FILE) or ".", "rate_limit_diagnostic.txt")


def _write_diagnostic(pattern: str, layer: str, matched_line: str, stdout: str, stderr: str) -> None:
    """Write a full diagnostic snapshot so the user can audit false-positive triggers."""
    os.makedirs(os.path.dirname(_DIAGNOSTIC_FILE) or ".", exist_ok=True)
    combined_tail = "\n".join((stdout + "\n" + stderr).splitlines()[-30:])
    with open(_DIAGNOSTIC_FILE, "w") as f:
        f.write(f"Triggered: {datetime.now(timezone.utc).isoformat()}\n")
        f.write(f"Layer: {layer}\n")
        f.write(f"Pattern: {pattern!r}\n")
        f.write(f"Matched line: {matched_line}\n\n")
        f.write("--- Last 30 lines searched ---\n")
        f.write(combined_tail + "\n\n")
        f.write("--- Full stdout (last 3000 chars) ---\n")
        f.write(stdout[-3000:] + "\n")
        f.write("--- Full stderr (last 1000 chars) ---\n")
        f.write(stderr[-1000:] + "\n")

# Layer 1: structural JSON field patterns — searched across all of stdout only.
# Matches the actual Claude API error format:
#   {"type":"error","error":{"type":"rate_limit_error","message":"..."}}
_STRUCTURAL_PATTERNS = [
    '"type": "rate_limit_error"',   # formatted JSON (space after colon)
    '"type":"rate_limit_error"',    # compact JSON (no space)
    '"type": "rate_limit_event"',   # event variant — formatted JSON
    '"type":"rate_limit_event"',    # event variant — compact JSON
]

# Layer 2: text patterns — searched in last 30 lines of stdout+stderr combined.
# "5 hour" / "5-hour" are intentionally excluded: they produce false positives
# on narrative text (e.g. "the 5-hour limit is a concern").  The real rate-limit
# events from the Claude CLI are caught by the Layer 1 structural patterns and
# by "rate limit" / "429" / "overloaded" below.
_TEXT_PATTERNS = [
    "rate limit",       # catches "API Error: Rate limit reached", "rate limit exceeded"
    "rate_limit_error", # catches raw JSON type value in text output
    "429",              # catches "HTTP 429" in verbose error traces
    "overloaded",       # catches overloaded_error type
    "usage limit",      # catches subscription-based "Claude AI usage limit" messages
]


class RateLimitError(Exception):
    """Raised when rate limit is hit and AUTONOMOUS_MODE is False."""


class RateLimiter:
    """
    Tracks hourly Claude API call counts and detects rate-limit signals.

    Args:
        hourly_call_limit: Max calls allowed per hour bucket. Defaults to
            HOURLY_CALL_LIMIT (10). Override in tests without monkey-patching.
        unattended_mode: If True, wait_for_reset() sleeps silently. If False,
            raises RateLimitError so a human can intervene. Defaults to reading
            the AUTONOMOUS_MODE flag.
        state_file: Path to the JSON state file. Defaults to STATE_FILE.
    """

    def __init__(
        self,
        hourly_call_limit: int = HOURLY_CALL_LIMIT,
        state_file: str = STATE_FILE,
        unattended_mode: bool | None = None,
        time_fn=None,
    ) -> None:
        self.hourly_call_limit = hourly_call_limit
        self.state_file = state_file
        if unattended_mode is None:
            unattended_mode = AUTONOMOUS_MODE
        self.unattended_mode = unattended_mode
        # Injectable clock — defaults to time.time; override in tests for determinism.
        self._time_fn = time_fn if time_fn is not None else time.time
        self._state: dict = {}
        self.init_call_tracking()

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _current_hour_bucket(self) -> str:
        return datetime.fromtimestamp(self._time_fn(), timezone.utc).strftime("%Y-%m-%dT%H")

    def _save_state(self) -> None:
        state_dir = os.path.dirname(self.state_file)
        if state_dir:
            os.makedirs(state_dir, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(self._state, f, indent=2)

    def init_call_tracking(self) -> None:
        """Load state from disk, or create a fresh state file if absent/corrupt."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    self._state = json.load(f)
            except (json.JSONDecodeError, OSError):
                logger.warning("rate_limiter_state.json corrupted — reinitialising")
                self._state = {}
        else:
            self._state = {}

        # Ensure all required keys exist (handles partial/corrupt files)
        self._state.setdefault("hour_bucket", self._current_hour_bucket())
        self._state.setdefault("calls_this_hour", 0)
        self._state.setdefault("last_reset_ts", self._time_fn())
        self._state.setdefault("rate_limit_cooldown_until", None)

        # Roll the bucket in case the state file is from a prior hour
        self._maybe_reset_bucket()
        self._save_state()

    def _maybe_reset_bucket(self) -> None:
        """If the UTC hour has advanced, reset the call counter."""
        current_bucket = self._current_hour_bucket()
        if self._state.get("hour_bucket") != current_bucket:
            logger.info(
                "RateLimiter: new hour bucket %s — resetting call counter.", current_bucket
            )
            self._state["hour_bucket"] = current_bucket
            self._state["calls_this_hour"] = 0
            self._state["last_reset_ts"] = self._time_fn()
            self._save_state()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def can_make_call(self) -> bool:
        """Return True if a call is permitted (hourly limit not exceeded and no active cooldown)."""
        self._maybe_reset_bucket()
        cooldown_until = self._state.get("rate_limit_cooldown_until")
        if cooldown_until is not None:
            if self._time_fn() < cooldown_until:
                return False
            # Cooldown expired — clear it
            self._state["rate_limit_cooldown_until"] = None
            self._save_state()
        return self._state.get("calls_this_hour", 0) < self.hourly_call_limit

    def record_call(self) -> None:
        """Increment the hourly call counter and persist state."""
        self._maybe_reset_bucket()
        self._state["calls_this_hour"] = self._state.get("calls_this_hour", 0) + 1
        self._save_state()

    def record_rate_limit_signal(self) -> None:
        """Set a 1-hour cooldown starting now and persist."""
        self._state["rate_limit_cooldown_until"] = self._time_fn() + COOLDOWN_SECONDS
        self._save_state()
        logger.warning("RateLimiter: rate-limit signal recorded — cooldown set for 1 hour.")

    def parse_output_for_limit(self, stdout: str, stderr: str) -> bool:
        """
        Inspect subprocess stdout/stderr for rate-limit signals.

        Layer 1 — Structural: search all of stdout for JSON field markers.
        Layer 2 — Text pattern: search the last 30 lines of stdout+stderr combined.

        Note: Layer 3 (exit-code check) is handled by the caller; this method only
        inspects the text content.

        Returns True if any signal is detected; False otherwise.
        """
        # Layer 1: structural JSON field search — stdout only to avoid false positives
        for pattern in _STRUCTURAL_PATTERNS:
            if pattern in stdout:
                match_line = next((l for l in stdout.splitlines() if pattern in l), "")
                logger.info("RateLimiter.parse_output_for_limit: structural match %r.", pattern)
                logger.info("RateLimiter: matched line → %s", match_line[:500])
                _write_diagnostic(pattern, "structural", match_line, stdout, stderr)
                return True

        # Layer 2: text-pattern search — only last 30 lines of combined output to
        # avoid triggering on narrative body text that mentions rate limits in passing.
        combined_lines = (stdout + "\n" + stderr).splitlines()
        last_30 = "\n".join(combined_lines[-30:]).lower()
        for pattern in _TEXT_PATTERNS:
            if pattern in last_30:
                match_line = next((l for l in last_30.splitlines() if pattern in l.lower()), "")
                logger.info("RateLimiter.parse_output_for_limit: text-pattern match %r.", pattern)
                logger.info("RateLimiter: matched line → %s", match_line[:500])
                logger.info("RateLimiter: last 30 lines searched:\n%s", last_30)
                _write_diagnostic(pattern, "text", match_line, stdout, stderr)
                return True

        return False

    def seconds_until_reset(self) -> float:
        """Return the remaining cooldown seconds (0.0 if not in cooldown)."""
        cooldown_until = self._state.get("rate_limit_cooldown_until")
        if cooldown_until is None:
            return 0.0
        return max(0.0, cooldown_until - self._time_fn())

    def clear_cooldown(self) -> None:
        """Clear the rate-limit cooldown immediately and persist."""
        self._state["rate_limit_cooldown_until"] = None
        self._save_state()
        logger.info("RateLimiter: cooldown cleared manually.")

    @property
    def api_calls_this_hour(self) -> int:
        """Current call count for the active hour bucket."""
        return self._state.get("calls_this_hour", 0)

    @property
    def rate_limit_cooldown_until(self) -> float | None:
        """Timestamp when the cooldown expires, or None if not in cooldown."""
        return self._state.get("rate_limit_cooldown_until")

    async def wait_for_reset(self) -> None:
        """
        Wait until the rate-limit cooldown expires.

        - unattended_mode=True:  sleep silently for the remaining cooldown duration.
        - unattended_mode=False: raise RateLimitError immediately for human intervention.

        After sleeping, clears the cooldown field and saves state.
        """
        cooldown_until = self._state.get("rate_limit_cooldown_until")
        if cooldown_until is None:
            return

        remaining = cooldown_until - self._time_fn()
        if remaining <= 0:
            self._state["rate_limit_cooldown_until"] = None
            self._save_state()
            return

        if not self.unattended_mode:
            raise RateLimitError(
                f"Rate limit active for another {remaining:.0f}s. "
                "Set --autonomous 1 to wait automatically, or clear the cooldown manually."
            )

        logger.warning(
            "RateLimiter: unattended mode — sleeping %.0fs until cooldown expires.", remaining
        )
        await asyncio.sleep(remaining)
        self._state["rate_limit_cooldown_until"] = None
        self._save_state()
        logger.info("RateLimiter: cooldown expired — calls permitted again.")
