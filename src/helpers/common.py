import asyncio
import os
import shutil
import logging
from datetime import datetime

from config import INTERACTIVE_PROMPT_PATTERNS, AUTONOMOUS_MODE, UNATTENDED_DEFAULTS


# ==========================================
# EXCEPTIONS (shared by all workflows)
# ==========================================

class CircuitBreakerOpenError(Exception):
    """Raised when the circuit breaker opens and AUTONOMOUS_MODE is False."""


# ==========================================
# INTERACTIVE PROMPT INTERCEPT (shared)
# ==========================================

async def _stream_with_intercept(
    process, worker_id: int, master_fd: int | None = None
) -> tuple[str, str]:
    """Stream worker stdout/stderr line-by-line; intercept interactive prompts.

    Replaces ``await process.communicate()`` so that prompts like SSH host-key
    confirmations or password requests are forwarded to the human (or answered
    automatically in AUTONOMOUS_MODE) instead of causing an indefinite hang.

    PTY mode (``master_fd`` provided):
        Prompt answers are written via ``os.write(master_fd, ...)`` — a
        synchronous syscall that works even when ``process.stdin`` is ``None``
        (which is the case when the subprocess was started with a raw fd rather
        than ``asyncio.subprocess.PIPE``).

    Legacy mode (``master_fd=None``):
        Falls back to ``process.stdin.write`` / ``drain()`` for callers that
        still use PIPE-based stdin (e.g. unit tests).

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
                    if AUTONOMOUS_MODE:
                        default_ans = (
                            UNATTENDED_DEFAULTS[idx]
                            if idx < len(UNATTENDED_DEFAULTS)
                            else "yes"
                        )
                        logging.warning(
                            "[WORKER %d PROMPT (unattended)]: %s → answering '%s'",
                            worker_id, text, default_ans,
                        )
                        if master_fd is not None:
                            try:
                                os.write(master_fd, (default_ans + "\n").encode())
                            except OSError as exc:
                                logging.warning("[WORKER %d] PTY write failed: %s", worker_id, exc)
                        elif process.stdin:
                            process.stdin.write((default_ans + "\n").encode())
                            await process.stdin.drain()
                    else:
                        print(f"\n[WORKER {worker_id} PROMPT]: {text}")
                        answer = input("Your answer: ").strip()
                        if master_fd is not None:
                            try:
                                os.write(master_fd, (answer + "\n").encode())
                            except OSError as exc:
                                logging.warning("[WORKER %d] PTY write failed: %s", worker_id, exc)
                        elif process.stdin:
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
# HELPER FUNCTIONS (shared by all workflows)
# ==========================================

def move_to_archive(file_path: str, archive_dir: str) -> None:
    """Move file_path into archive_dir with a timestamp suffix.
    No-ops silently if the file does not exist."""
    import os
    import shutil
    from datetime import datetime

    if not os.path.exists(file_path):
        return
    base, ext = os.path.splitext(os.path.basename(file_path))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(archive_dir, f"{base}_{timestamp}{ext}")
    shutil.move(file_path, dest)
