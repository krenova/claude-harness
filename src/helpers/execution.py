import json
import os
import shutil
import subprocess
import glob
import logging
from pathlib import Path

from config import PATH_LIVE_ARTIFACTS


# ==========================================
# HELPER FUNCTIONS (execution-only)
# ==========================================
# Used exclusively by src/workflows/execution.py
# ==========================================

def _get_baseline_commit() -> str:
    """Return the current HEAD commit hash, or '' if git is unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def count_git_diff_files(baseline_commit: str) -> int:
    """Count files changed since baseline_commit (0 if git unavailable or no baseline)."""
    if not baseline_commit:
        return 0
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", baseline_commit],
            capture_output=True, text=True, timeout=10,
        )
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        return len(lines)
    except Exception:
        return 0


def count_new_artifacts(loop_num: int) -> int:
    """Count worker stdout files produced for the given loop number."""
    pattern = f"{PATH_LIVE_ARTIFACTS}/worker_{loop_num}_*_stdout.txt"
    return len(glob.glob(pattern))



def extract_error_signature(outputs: list[str]) -> str | None:
    """Return a truncated first error-like line found across worker outputs, or None."""
    for output in outputs:
        for line in output.splitlines():
            lower = line.lower()
            if any(kw in lower for kw in ("error", "exception", "traceback", "failed")):
                return line[:200]
    return None


# ==========================================
# EXECUTION STATE HELPERS
# ==========================================

def load_execution_state(state_file: str) -> dict | None:
    """Return parsed execution state dict or None if file absent/corrupt."""
    if not os.path.exists(state_file):
        return None
    try:
        with open(state_file, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_execution_state(
    state_file: str,
    completed_phases: list[str],
    current_phase: str,
    current_loop: int,
) -> None:
    """Write execution_state.json atomically."""
    tmp = state_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump(
            {
                "status": "in_progress",
                "completed_phases": completed_phases,
                "current_phase": current_phase,
                "current_loop": current_loop,
            },
            f,
        )
    os.replace(tmp, state_file)


def clean_transient_artifacts():
    """Clean transient artifacts from .artifacts/live_artifacts directory. Phase reports are never deleted."""
    artifacts_dir = Path(PATH_LIVE_ARTIFACTS)
    if not artifacts_dir.exists():
        return

    transient_patterns = [
        "worker_*.txt",
        "status.json",
        "rate_limiter_state.json",
        "circuit_breaker_state.json",
        "rate_limit_diagnostic.txt",
    ]

    for pattern in transient_patterns:
        for f in artifacts_dir.glob(pattern):
            try:
                f.unlink()
                logging.info(f"🗑️ Cleaned transient artifact: {f.name}")
            except OSError as exc:
                logging.warning(f"⚠️ Could not delete {f.name}: {exc}")
