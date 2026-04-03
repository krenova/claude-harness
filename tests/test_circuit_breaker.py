"""
tests/test_circuit_breaker.py

Unit tests for src/safeguards/circuit_breaker.py.

All tests use isolated temp directories (KPI-4.5) and a deterministic
time_fn so cooldown behaviour can be verified without sleeping.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.safeguards.circuit_breaker import CircuitBreaker


def _make_cb(tmp_dir, cooldown=100, t=None, **kwargs):
    """Convenience factory with isolated state path."""
    state_path = Path(tmp_dir) / "cb_state.json"
    time_fn = (lambda: t[0]) if t is not None else None
    return CircuitBreaker(
        state_path=state_path,
        no_progress_threshold=3,
        same_error_threshold=5,
        cooldown_seconds=cooldown,
        time_fn=time_fn,
        **kwargs,
    )


def _no_progress(cb, n=1):
    """Call record_loop_result with zero progress n times."""
    for _ in range(n):
        cb.record_loop_result(
            files_changed=0,
            worker_artifacts_produced=0,
            kpi_advancement=False,
            error_signature=None,
        )


class TestCircuitBreaker(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # KPI-1.4 / basic state transitions
    # ------------------------------------------------------------------

    def test_closed_by_default(self):
        """Fresh CircuitBreaker starts CLOSED."""
        cb = _make_cb(self.tmp_dir)
        self.assertFalse(cb.is_open())
        self.assertEqual(cb.get_state(), "CLOSED")

    def test_open_on_no_progress(self):
        """3 consecutive no-progress loops → OPEN."""
        cb = _make_cb(self.tmp_dir)
        _no_progress(cb, 3)
        self.assertTrue(cb.is_open())

    def test_progress_resets_counter(self):
        """2× no-progress, then 1× progress, then 2× no-progress → still CLOSED."""
        cb = _make_cb(self.tmp_dir)
        _no_progress(cb, 2)
        cb.record_loop_result(files_changed=1, worker_artifacts_produced=0,
                               kpi_advancement=False, error_signature=None)
        _no_progress(cb, 2)
        self.assertFalse(cb.is_open())

    # ------------------------------------------------------------------
    # KPI-1.6 / same-error rule
    # ------------------------------------------------------------------

    def test_open_on_same_error(self):
        """5 consecutive calls with identical error_signature → OPEN."""
        cb = _make_cb(self.tmp_dir)
        for _ in range(5):
            cb.record_loop_result(0, 0, False, "err_abc")
        self.assertTrue(cb.is_open())

    def test_different_errors_dont_trigger(self):
        """5 calls, each with a unique error signature, should NOT open the CB.

        files_changed=1 on each call ensures the no-progress counter stays at 0
        so only the same-error rule is being exercised here.
        """
        cb = _make_cb(self.tmp_dir)
        for i in range(5):
            cb.record_loop_result(
                files_changed=1,        # progress present — no-progress rule not triggered
                worker_artifacts_produced=0,
                kpi_advancement=False,
                error_signature=f"unique_err_{i}",
            )
        self.assertFalse(cb.is_open())

    # ------------------------------------------------------------------
    # KPI-1.5 / cooldown and recovery
    # ------------------------------------------------------------------

    def test_half_open_after_cooldown(self):
        """OPEN + cooldown elapsed → check_cooldown() → HALF_OPEN."""
        t = [time.time()]
        cb = _make_cb(self.tmp_dir, cooldown=100, t=t)
        _no_progress(cb, 3)
        self.assertTrue(cb.is_open())

        t[0] += 101           # advance mock clock past cooldown
        result = cb.check_cooldown()
        self.assertTrue(result)
        self.assertEqual(cb.get_state(), "HALF_OPEN")

    def test_closed_after_recovery(self):
        """HALF_OPEN + record_loop_result with progress → CLOSED."""
        t = [time.time()]
        cb = _make_cb(self.tmp_dir, cooldown=100, t=t)
        _no_progress(cb, 3)
        t[0] += 101
        cb.check_cooldown()

        cb.record_loop_result(files_changed=1, worker_artifacts_produced=0,
                               kpi_advancement=False, error_signature=None)
        self.assertEqual(cb.get_state(), "CLOSED")

    def test_reopen_if_no_recovery(self):
        """HALF_OPEN + record_loop_result with no progress → back to OPEN."""
        t = [time.time()]
        cb = _make_cb(self.tmp_dir, cooldown=100, t=t)
        _no_progress(cb, 3)
        t[0] += 101
        cb.check_cooldown()

        _no_progress(cb, 1)
        self.assertEqual(cb.get_state(), "OPEN")

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def test_state_persistence(self):
        """OPEN state survives a fresh CircuitBreaker loaded from the same file."""
        state_path = Path(self.tmp_dir) / "cb_persist.json"
        cb1 = CircuitBreaker(state_path=state_path, no_progress_threshold=3,
                              same_error_threshold=5, cooldown_seconds=100)
        _no_progress(cb1, 3)
        self.assertTrue(cb1.is_open())

        cb2 = CircuitBreaker(state_path=state_path, no_progress_threshold=3,
                              same_error_threshold=5, cooldown_seconds=100)
        self.assertTrue(cb2.is_open())


if __name__ == "__main__":
    unittest.main()
