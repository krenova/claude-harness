"""
tests/test_integration.py

Integration test: run the full orchestrator (--skip-planning) in a temporary
working directory against a trivial single-task plan.

This test makes real Claude CLI calls and is SKIPPED by default.
Enable with:  AMA_INTEGRATION_TEST=1 python -m unittest tests.test_integration

Guards:
  1. Wall-clock timeout of 300 s (signal.SIGALRM / threading.Timer fallback).
  2. RateLimiter pre-seeded so at most 3 real API calls are consumed.
"""

import json
import os
import shutil
import signal
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORCHESTRATOR = os.path.join(PROJECT_ROOT, "ama_orchestrator.py")

WALL_CLOCK_LIMIT = 300     # seconds
MAX_PROGRAM_CALLS    = 3       # consumed before the test; leaves HOURLY_PROGRAM_LIMIT - 3 free
HOURLY_LIMIT     = 10      # must match src/safeguards/rate_limiter.py HOURLY_PROGRAM_LIMIT

TRIVIAL_PLAN = """\
# Phase 1: Hello World

## Goal
Write the string "hello world" (exactly, no trailing newline) to the file
`.artifacts/hello.txt`.

## Tasks
- Write "hello world" to `.artifacts/hello.txt`.

## KPIs
- [ ] **KPI-1.1**: `.artifacts/hello.txt` exists and its contents equal `hello world`.
"""


def _timeout_handler(signum, frame):
    raise TimeoutError(f"Integration test exceeded {WALL_CLOCK_LIMIT}s wall-clock limit")


def _apply_timeout():
    """Apply a wall-clock timeout using SIGALRM (Unix) or a threading.Timer (fallback)."""
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(WALL_CLOCK_LIMIT)
        return None
    else:
        # Windows fallback
        timer = threading.Timer(WALL_CLOCK_LIMIT, lambda: (_ for _ in ()).throw(
            TimeoutError(f"Integration test exceeded {WALL_CLOCK_LIMIT}s")
        ))
        timer.daemon = True
        timer.start()
        return timer


def _cancel_timeout(timer):
    if hasattr(signal, "SIGALRM"):
        signal.alarm(0)
    elif timer is not None:
        timer.cancel()


@unittest.skipUnless(
    os.environ.get("AMA_INTEGRATION_TEST"),
    "Integration test skipped — set AMA_INTEGRATION_TEST=1 to enable",
)
class TestIntegration(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="ama_integration_")
        # Create directory structure expected by the orchestrator
        for d in ("plans", ".artifacts/live_artifacts", ".logs"):
            os.makedirs(os.path.join(self.tmp_dir, d), exist_ok=True)

        # Write the trivial phase plan
        plan_path = os.path.join(self.tmp_dir, "plans", "phase_1_plan.md")
        Path(plan_path).write_text(TRIVIAL_PLAN)

        # Pre-seed rate_limiter_state.json so at most MAX_PROGRAM_CALLS remain
        # (fills HOURLY_LIMIT - MAX_PROGRAM_CALLS slots in the current hour bucket).
        from src.safeguards.rate_limiter import HOURLY_PROGRAM_LIMIT
        pre_consumed = max(0, HOURLY_PROGRAM_LIMIT - MAX_PROGRAM_CALLS)
        from datetime import datetime, timezone
        bucket = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        rl_state = {
            "hour_bucket": bucket,
            "calls_this_hour": pre_consumed,
            "last_reset_ts": time.time(),
            "rate_limit_cooldown_until": None,
        }
        rl_path = os.path.join(self.tmp_dir, ".artifacts/live_artifacts", "rate_limiter_state.json")
        Path(rl_path).write_text(json.dumps(rl_state, indent=2))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_trivial_plan(self):
        """Trivial 'hello world' plan completes cleanly with all safeguards engaged."""
        timer = _apply_timeout()
        try:
            env = {
                **os.environ,
                "AMA_AUTONOMOUS": "1",
                "PYTHONPATH": PROJECT_ROOT,
            }
            result = subprocess.run(
                [sys.executable, ORCHESTRATOR, "--skip-planning"],
                cwd=self.tmp_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=WALL_CLOCK_LIMIT,
            )

            # No Python tracebacks in stderr
            self.assertNotIn(
                "Traceback (most recent call last)",
                result.stderr,
                msg=f"Unexpected traceback in stderr:\n{result.stderr[:2000]}",
            )

            # status.json must exist and contain required fields
            status_path = os.path.join(self.tmp_dir, ".artifacts/live_artifacts", "status.json")
            self.assertTrue(
                os.path.exists(status_path),
                ".artifacts/status.json was not created",
            )
            status = json.loads(Path(status_path).read_text())
            for field in ("phase", "loop_count", "program_calls_this_hour",
                          "circuit_breaker_state", "exit_gate_heuristic_score",
                          "exit_gate_kpis_met", "active_workers",
                          "rate_limit_cooldown_until", "updated_at"):
                self.assertIn(field, status, f"Missing field '{field}' in status.json")

            # Log file must reference all three safeguards
            log_path = os.path.join(self.tmp_dir, ".logs", "orchestrator.log")
            if os.path.exists(log_path):
                log_text = Path(log_path).read_text()
                for keyword in ("RateLimiter", "CircuitBreaker", "ExitGate"):
                    self.assertIn(
                        keyword, log_text,
                        f"Expected '{keyword}' in orchestrator.log",
                    )

            # hello.txt may or may not exist depending on whether Claude succeeded,
            # but at minimum the orchestrator must have exited cleanly (code 0 or 1).
            self.assertIn(
                result.returncode, (0, 1),
                f"Unexpected exit code {result.returncode}",
            )

        finally:
            _cancel_timeout(timer)


if __name__ == "__main__":
    unittest.main()
