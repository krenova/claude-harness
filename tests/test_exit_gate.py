"""
tests/test_exit_gate.py

Unit tests for ama_safeguards/exit_gate.py.

ExitGate has no I/O side effects so no temp directories are needed;
state is entirely in-memory.
"""

import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ama_safeguards.exit_gate import ExitGate, ExitGateState


class TestExitGate(unittest.TestCase):

    def setUp(self):
        self.gate = ExitGate()

    # ------------------------------------------------------------------
    # KPI-1.7 / heuristic-only does not exit
    # ------------------------------------------------------------------

    def test_no_exit_heuristic_only(self):
        """heuristic_score >= 2 but kpis_met=False → should_exit() is False."""
        self.gate.record_worker_outputs(["done\ntask complete"])
        self.assertGreaterEqual(self.gate.get_state().heuristic_score, 2)
        self.assertFalse(self.gate.get_state().kpis_met_confirmed)
        self.assertFalse(self.gate.should_exit())

    # ------------------------------------------------------------------
    # KPI-1.8 / kpi-only does not exit
    # ------------------------------------------------------------------

    def test_no_exit_kpis_only(self):
        """kpis_met=True but heuristic_score=0 → should_exit() is False."""
        self.gate.record_kpi_review(kpis_met=True)
        self.assertEqual(self.gate.get_state().heuristic_score, 0)
        self.assertFalse(self.gate.should_exit())

    # ------------------------------------------------------------------
    # KPI-1.9 / both conditions → exit
    # ------------------------------------------------------------------

    def test_exit_both_conditions(self):
        """heuristic_score >= 2 AND kpis_met=True → should_exit() is True."""
        self.gate.record_worker_outputs(["done\ntask complete"])
        self.gate.record_kpi_review(kpis_met=True)
        self.assertTrue(self.gate.should_exit())

    # ------------------------------------------------------------------
    # Heuristic scoring
    # ------------------------------------------------------------------

    def test_heuristic_scoring(self):
        """Three distinct keywords across three outputs → heuristic_score == 3."""
        self.gate.record_worker_outputs(["task complete", "all tests pass", "done"])
        self.assertEqual(self.gate.get_state().heuristic_score, 3)

    def test_heuristic_case_insensitive(self):
        """'TASK COMPLETE' (all caps) is counted as a completion signal."""
        self.gate.record_worker_outputs(["TASK COMPLETE"])
        self.assertGreater(self.gate.get_state().heuristic_score, 0)

    # ------------------------------------------------------------------
    # KPI-1.10 / safety breaker
    # ------------------------------------------------------------------

    def test_safety_breaker_5_signals(self):
        """consecutive_completion_signals >= 5 → force exit with a WARNING."""
        self.gate.restore_state(
            ExitGateState(
                consecutive_completion_signals=5,
                kpis_met_confirmed=False,
                heuristic_score=0,
            )
        )
        with self.assertLogs(level=logging.WARNING) as log_ctx:
            result = self.gate.should_exit()

        self.assertTrue(result)
        self.assertTrue(
            any("safety breaker" in msg.lower() or "consecutive" in msg.lower()
                for msg in log_ctx.output),
            msg="Expected a safety-breaker WARNING in the log output",
        )

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def test_reset_clears_state(self):
        """reset() returns gate to initial state so should_exit() is False again."""
        self.gate.record_worker_outputs(["done\ntask complete"])
        self.gate.record_kpi_review(kpis_met=True)
        self.assertTrue(self.gate.should_exit())

        self.gate.reset()
        self.assertFalse(self.gate.should_exit())
        state = self.gate.get_state()
        self.assertEqual(state.heuristic_score, 0)
        self.assertFalse(state.kpis_met_confirmed)
        self.assertEqual(state.consecutive_completion_signals, 0)


if __name__ == "__main__":
    unittest.main()
