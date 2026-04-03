import asyncio
import json
import os
import shutil
import subprocess
import glob
import logging
from datetime import datetime

from config import (
    PATH_ARTIFACTS,
    INTERACTIVE_PROMPT_PATTERNS,
    UNATTENDED_MODE,
    UNATTENDED_DEFAULTS,
)


# ==========================================
# EXCEPTIONS
# ==========================================

class CircuitBreakerOpenError(Exception):
    """Raised when the circuit breaker opens and UNATTENDED_MODE is False."""


# ==========================================
# INTERACTIVE PROMPT INTERCEPT
# ==========================================

async def _stream_with_intercept(process, worker_id: int) -> tuple[str, str]:
    """Stream worker stdout/stderr line-by-line; intercept interactive prompts.

    Replaces ``await process.communicate()`` so that prompts like SSH host-key
    confirmations or password requests are forwarded to the human (or answered
    automatically in UNATTENDED_MODE) instead of causing an indefinite hang.

    Returns:
        (stdout_text, stderr_text) — full captured output as strings.
    """
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    async def read_stream(stream, lines_buf: list[str]) -> None:
        if stream is None:
            return
        async for line in stream:
            text = line.decode("utf-8", errors="replace").rstrip()
            lines_buf.append(text)
            for idx, pattern in enumerate(INTERACTIVE_PROMPT_PATTERNS):
                if pattern.search(text):
                    if UNATTENDED_MODE:
                        default_ans = (
                            UNATTENDED_DEFAULTS[idx]
                            if idx < len(UNATTENDED_DEFAULTS)
                            else "yes"
                        )
                        logging.warning(
                            "[WORKER %d PROMPT (unattended)]: %s → answering '%s'",
                            worker_id, text, default_ans,
                        )
                        if process.stdin:
                            process.stdin.write((default_ans + "\n").encode())
                            await process.stdin.drain()
                    else:
                        print(f"\n[WORKER {worker_id} PROMPT]: {text}")
                        answer = input("Your answer: ").strip()
                        if process.stdin:
                            process.stdin.write((answer + "\n").encode())
                            await process.stdin.drain()
                    break

    await asyncio.gather(
        read_stream(process.stdout, stdout_lines),
        read_stream(process.stderr, stderr_lines),
    )
    await process.wait()
    return "\n".join(stdout_lines), "\n".join(stderr_lines)


# ==========================================
# HELPER FUNCTIONS
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


def extract_error_signature(outputs: list[str]) -> str | None:
    """Return a truncated first error-like line found across worker outputs, or None."""
    for output in outputs:
        for line in output.splitlines():
            lower = line.lower()
            if any(kw in lower for kw in ("error", "exception", "traceback", "failed")):
                return line[:200]
    return None


# ==========================================
# PLANNING STATE HELPERS
# ==========================================

def load_planning_state(state_file: str) -> dict | None:
    """Return parsed state dict or None if file absent/corrupt."""
    if not os.path.exists(state_file):
        return None
    try:
        with open(state_file, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_planning_state(state_file: str, status: str, iteration: int) -> None:
    """Write planning_state.json atomically."""
    tmp = state_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"status": status, "iteration": iteration}, f)
    os.replace(tmp, state_file)


def clear_planning_state(state_file: str) -> None:
    """Delete the state file (called on approval)."""
    try:
        os.remove(state_file)
    except FileNotFoundError:
        pass


def move_to_archive(file_path: str, archive_dir: str) -> None:
    """Move file_path into archive_dir with a timestamp suffix.
    No-ops silently if the file does not exist."""
    if not os.path.exists(file_path):
        return
    base, ext = os.path.splitext(os.path.basename(file_path))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(archive_dir, f"{base}_{timestamp}{ext}")
    shutil.move(file_path, dest)


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
