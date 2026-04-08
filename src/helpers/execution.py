import json
import os
import shutil
import subprocess
import glob
import logging
from pathlib import Path

from config import PATH_ARTIFACTS


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
    pattern = f"{PATH_ARTIFACTS}/worker_{loop_num}_*_stdout.txt"
    return len(glob.glob(pattern))


def parse_review_file(path: str) -> dict | None:
    """Parse a review markdown file into a dict with kpis_met, any_new_kpi_satisfied, summary, proposed_fixes_or_new_kpis.

    Returns None if the file is absent or unreadable.
    """
    if not os.path.exists(path):
        return None
    try:
        raw = open(path).read()
    except OSError:
        return None

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip()
            sections[current] = []
        elif current is not None:
            # Skip blank lines — only accumulate meaningful content
            if stripped or sections[current]:
                sections[current].append(stripped)

    def get_value(key: str) -> str:
        """Return all content in a section, joined by newlines."""
        lines = sections.get(key, [])
        return "\n".join(lines).strip()

    def get_bool(key: str) -> bool:
        """Return the first line of a section as a boolean (true/false)."""
        first_line = sections.get(key, [""])[0].strip().lower()
        return first_line == "true"

    return {
        "kpis_met": get_bool("KPIs Met"),
        "any_new_kpi_satisfied": get_bool("Any New KPI Satisfied"),
        "summary": get_value("Summary"),
        "proposed_fixes_or_new_kpis": get_value("Proposed Fixes or New KPIs"),
    }


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
    """Clean transient artifacts from .artifacts directory. Phase reports are never deleted."""
    artifacts_dir = Path(PATH_ARTIFACTS)
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
            f.unlink()
            logging.info(f"🗑️ Cleaned transient artifact: {f.name}")
