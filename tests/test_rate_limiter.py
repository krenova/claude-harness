"""
tests/test_rate_limiter.py

Unit tests for ama_safeguards/rate_limiter.py.

All tests use isolated temp directories (KPI-4.5) and a deterministic
time_fn (no time.sleep required).
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ama_safeguards.rate_limiter import RateLimiter, COOLDOWN_SECONDS


def _make_rl(tmp_dir, limit=100, t=None, **kwargs):
    """Convenience factory: RateLimiter with a temp state file."""
    state_file = os.path.join(tmp_dir, "rate_limiter_state.json")
    time_fn = (lambda: t[0]) if t is not None else None
    return RateLimiter(
        hourly_call_limit=limit,
        state_file=state_file,
        time_fn=time_fn,
        **kwargs,
    )


class TestRateLimiter(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # KPI-1.1 / call-limit logic
    # ------------------------------------------------------------------

    def test_can_make_call_under_limit(self):
        """50 calls against limit=100 → can_make_call() is True."""
        rl = _make_rl(self.tmp_dir, limit=100)
        for _ in range(50):
            rl.record_call()
        self.assertTrue(rl.can_make_call())

    def test_can_make_call_at_limit(self):
        """Exactly 100 calls against limit=100 → can_make_call() is False."""
        rl = _make_rl(self.tmp_dir, limit=100)
        for _ in range(100):
            rl.record_call()
        self.assertFalse(rl.can_make_call())

    def test_reset_on_new_hour(self):
        """After 100 calls, advancing the mock clock +3601 s resets the bucket."""
        t = [time.time()]
        rl = _make_rl(self.tmp_dir, limit=100, t=t)
        for _ in range(100):
            rl.record_call()
        self.assertFalse(rl.can_make_call())

        t[0] += 3601          # jump into the next UTC hour
        self.assertTrue(rl.can_make_call())

    # ------------------------------------------------------------------
    # KPI-1.2 / parse_output_for_limit
    # ------------------------------------------------------------------

    def test_parse_rate_limit_json(self):
        """Layer 1: structural match on 'type': 'rate_limit_event'."""
        rl = _make_rl(self.tmp_dir)
        stdout = '{"type": "rate_limit_event", "message": "slow down"}'
        self.assertTrue(rl.parse_output_for_limit(stdout, ""))

    def test_parse_is_error_json(self):
        """Layer 1: structural match on 'is_error': true."""
        rl = _make_rl(self.tmp_dir)
        stdout = '{"is_error": true, "error": {"type": "overloaded_error"}}'
        self.assertTrue(rl.parse_output_for_limit(stdout, ""))

    def test_parse_429_in_stderr(self):
        """Layer 2: text-pattern match on '429' in stderr."""
        rl = _make_rl(self.tmp_dir)
        self.assertTrue(rl.parse_output_for_limit("", "HTTP 429 Too Many Requests"))

    def test_no_false_positive_narrative(self):
        """'5-hour limit' in narrative text must NOT trigger a rate-limit signal."""
        rl = _make_rl(self.tmp_dir)
        # Deliberately uses "5-hour" in narrative context only — no real API error signals.
        # Does NOT contain "rate limit", "429", "overloaded", or structural JSON fields.
        stdout = "the 5-hour limit is a concern and we do not expect any throttling"
        self.assertFalse(rl.parse_output_for_limit(stdout, ""))

    # ------------------------------------------------------------------
    # KPI-1.3 / state persistence
    # ------------------------------------------------------------------

    def test_state_persistence(self):
        """10 recorded calls survive a fresh RateLimiter loaded from the same file."""
        fixed = time.time()
        t = [fixed]
        state_file = os.path.join(self.tmp_dir, "rl_state.json")

        rl1 = RateLimiter(hourly_call_limit=100, state_file=state_file, time_fn=lambda: t[0])
        for _ in range(10):
            rl1.record_call()

        rl2 = RateLimiter(hourly_call_limit=100, state_file=state_file, time_fn=lambda: t[0])
        self.assertEqual(rl2._state["calls_this_hour"], 10)

    def test_cooldown_set_on_signal(self):
        """record_rate_limit_signal() sets cooldown_until to now + 3600."""
        fixed = 1_000_000.0
        rl = _make_rl(self.tmp_dir, t=[fixed])
        rl.record_rate_limit_signal()
        self.assertEqual(
            rl._state["rate_limit_cooldown_until"],
            fixed + COOLDOWN_SECONDS,
        )

    # ------------------------------------------------------------------
    # Extra: seconds_until_reset / clear_cooldown
    # ------------------------------------------------------------------

    def test_seconds_until_reset_no_cooldown(self):
        """Returns 0.0 when no cooldown is active."""
        rl = _make_rl(self.tmp_dir)
        self.assertEqual(rl.seconds_until_reset(), 0.0)

    def test_seconds_until_reset_with_cooldown(self):
        """Returns remaining seconds when cooldown is active."""
        fixed = 1_000_000.0
        rl = _make_rl(self.tmp_dir, t=[fixed])
        rl.record_rate_limit_signal()
        self.assertAlmostEqual(rl.seconds_until_reset(), COOLDOWN_SECONDS, delta=1)

    def test_clear_cooldown_removes_block(self):
        """clear_cooldown() lets can_make_call() return True again."""
        fixed = 1_000_000.0
        rl = _make_rl(self.tmp_dir, t=[fixed])
        rl.record_rate_limit_signal()
        self.assertFalse(rl.can_make_call())
        rl.clear_cooldown()
        self.assertTrue(rl.can_make_call())


if __name__ == "__main__":
    unittest.main()
