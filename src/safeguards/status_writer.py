"""
src/safeguards/status_writer.py

Writes .artifacts/status.json after each orchestration loop to enable
real-time monitoring and crash recovery.

Also provides a WorkerRegistry for tracking active worker IDs so that
active_workers is accurate during concurrent execution.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STATUS_FILE = ".artifacts/status.json"

# ---------------------------------------------------------------------------
# Worker Registry (async-safe via asyncio.Lock)
# ---------------------------------------------------------------------------

_worker_registry: dict[int, str] = {}  # worker_id → current task description
_registry_lock = asyncio.Lock()


async def register_worker(worker_id: int, task: str) -> None:
    """Register a worker as active. Call at the start of run_worker_agent."""
    async with _registry_lock:
        _worker_registry[worker_id] = task


async def deregister_worker(worker_id: int) -> None:
    """Deregister a worker. Call in a finally block in run_worker_agent."""
    async with _registry_lock:
        _worker_registry.pop(worker_id, None)


def get_active_workers() -> list[int]:
    """Return a snapshot of currently active worker IDs (not async-safe, for status reads)."""
    return list(_worker_registry.keys())


# ---------------------------------------------------------------------------
# Status writer
# ---------------------------------------------------------------------------

def write_status(
    phase: str,
    loop_count: int,
    program_calls_this_hour: int,
    circuit_breaker_state: str,
    exit_gate_heuristic: int,
    exit_gate_kpis_met: bool,
    active_workers: list[int],
    rate_limit_cooldown_until: float | None,
    hourly_call_limit: int | None = None,
    status_file: str = STATUS_FILE,
) -> None:
    """Write current orchestration state to .artifacts/status.json.

    Called at the end of each orchestration loop iteration. Enables monitoring dashboards and
    crash recovery (ExitGate state can be restored from this file on restart).

    Schema::

        {
          "phase": "execution",
          "loop_count": 7,
          "program_calls_this_hour": 23,
          "hourly_call_limit": 50,
          "circuit_breaker_state": "CLOSED",
          "exit_gate_heuristic_score": 1,
          "exit_gate_kpis_met": false,
          "active_workers": [1, 3],
          "rate_limit_cooldown_until": null,
          "updated_at": "2026-03-29T14:32:00Z"
        }
    """
    status_dir = os.path.dirname(status_file)
    if status_dir:
        os.makedirs(status_dir, exist_ok=True)

    payload = {
        "phase": phase,
        "loop_count": loop_count,
        "program_calls_this_hour": program_calls_this_hour,
        "hourly_call_limit": hourly_call_limit,
        "circuit_breaker_state": circuit_breaker_state,
        "exit_gate_heuristic_score": exit_gate_heuristic,
        "exit_gate_kpis_met": exit_gate_kpis_met,
        "active_workers": active_workers,
        "rate_limit_cooldown_until": rate_limit_cooldown_until,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        with open(status_file, "w") as f:
            json.dump(payload, f, indent=2)
        logger.debug("StatusWriter: wrote %s", status_file)
    except OSError as exc:
        logger.error("StatusWriter: failed to write %s: %s", status_file, exc)
