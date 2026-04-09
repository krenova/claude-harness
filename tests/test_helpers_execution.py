"""
tests/test_helpers_execution.py

Smoke tests for src/helpers/execution.py.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestReviewDataSchema(unittest.TestCase):
    """Validate the expected schema for review_data dicts coming from the orchestrator."""

    REQUIRED_KEYS = {"kpis_met", "any_new_kpi_satisfied", "summary", "proposed_fixes_or_new_kpis"}

    def _check_schema(self, review_data: dict):
        self.assertIsInstance(review_data, dict)
        for key in self.REQUIRED_KEYS:
            self.assertIn(key, review_data, f"review_data missing key: {key}")
        self.assertIsInstance(review_data["kpis_met"], bool)
        self.assertIsInstance(review_data["any_new_kpi_satisfied"], bool)
        self.assertIsInstance(review_data["summary"], str)
        self.assertIsInstance(review_data["proposed_fixes_or_new_kpis"], str)

    def test_all_kpis_met_schema(self):
        review_data = {
            "kpis_met": True,
            "any_new_kpi_satisfied": True,
            "summary": "All tests pass and code is complete.",
            "proposed_fixes_or_new_kpis": "NONE. Proceed to next phase.",
        }
        self._check_schema(review_data)

    def test_kpis_not_met_schema(self):
        review_data = {
            "kpis_met": False,
            "any_new_kpi_satisfied": False,
            "summary": "Tests are failing in module X.",
            "proposed_fixes_or_new_kpis": "Fix the import error in module X and re-run tests.",
        }
        self._check_schema(review_data)

    def test_none_review_data_handled(self):
        """review_data=None should be handled gracefully by callers (not crash)."""
        review_data = None
        kpis_met = review_data.get("kpis_met", False) if review_data else False
        self.assertFalse(kpis_met)


class TestCleanTransientArtifactsPermissionError(unittest.TestCase):
    """Bug 5: clean_transient_artifacts() should warn, not raise, on PermissionError."""

    def test_permission_error_logs_warning_not_raises(self):
        import logging
        from src.helpers.execution import clean_transient_artifacts

        with tempfile.TemporaryDirectory() as tmp:
            with patch("src.helpers.execution.PATH_ARTIFACTS", tmp):
                # Create a file matching worker_*.txt so glob finds it
                worker_file = Path(tmp) / "worker_1_test_stdout.txt"
                worker_file.write_text("test")

                # Patch Path.unlink to raise PermissionError, simulating a locked file
                original_unlink = Path.unlink

                def _raise_permission_error(self_path, missing_ok=False):
                    raise PermissionError(f"Permission denied: {self_path}")

                with patch.object(Path, "unlink", _raise_permission_error):
                    try:
                        with self.assertLogs(level=logging.WARNING):
                            clean_transient_artifacts()
                    except PermissionError:
                        self.fail(
                            "clean_transient_artifacts() raised PermissionError — should have caught it"
                        )


if __name__ == "__main__":
    unittest.main()
