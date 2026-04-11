import asyncio
import logging
import os
import time
from pathlib import Path

from config import PATH_LIVE_ARTIFACTS, PATH_ARCHIVED_ARTIFACTS, MAX_TURNS
from src.helpers import _stream_with_intercept, move_to_archive
from src.safeguards import RateLimiter
from src.safeguards.status_writer import register_worker, deregister_worker
from src.prompts.loader import load_prompt

_WORKER_PROMPTS = Path(__file__).parent.parent / "prompts" / "agents" / "worker.yaml"


async def run_worker_agent(
    sem,
    worker_id: int,
    task_prompt: str,
    loop_num: int = 0,
    rate_limiter: RateLimiter | None = None,
    max_turns: str = MAX_TURNS,
) -> str:
    """Runs an independent worker agent asynchronously, bounded by a Semaphore."""
    async with sem:
        # Rate limiter: wait until a call slot is available
        if rate_limiter:
            while not rate_limiter.can_make_call():
                await asyncio.sleep(1)
            rate_limiter.record_call()

        await register_worker(worker_id, task_prompt)
        try:
            preview = task_prompt[:100].replace("\n", " ")
            logging.info(f"  🚀 [WORKER {worker_id}] Starting task: {preview}...")
            t0 = time.time()
            full_prompt = load_prompt(
                _WORKER_PROMPTS, "worker_task",
                worker_id=worker_id,
                task=task_prompt,
                path_artifacts=PATH_LIVE_ARTIFACTS,
            )

            # Open a PTY. slave_fd is the child's stdin — claude sees a real TTY and
            # executes immediately. master_fd is kept in the parent to write prompt answers.
            master_fd = -1   # sentinel: guards os.close() in finally if openpty() fails
            master_fd, slave_fd = os.openpty()
            try:
                process = await asyncio.create_subprocess_exec(
                    "claude", "-p", full_prompt, "--dangerously-skip-permissions",
                    "--max-turns", max_turns, "--sandbox",
                    stdin=slave_fd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            finally:
                os.close(slave_fd)   # Child holds its own copy; parent closes immediately

            stdout_text, stderr_text = await _stream_with_intercept(
                process, worker_id, master_fd=master_fd
            )
            elapsed = time.time() - t0

            # Check for rate-limit signals in subprocess output
            if rate_limiter and rate_limiter.parse_output_for_limit(stdout_text, stderr_text):
                rate_limiter.record_rate_limit_signal()

            if stderr_text.strip():
                logging.warning(f"  [WORKER {worker_id}] stderr: {stderr_text.strip()[:300]}")

            # Persist raw stdout/stderr for debugging and circuit-breaker analysis.
            # Including loop_num prevents per-loop overwrites (full history for post-mortem).
            stdout_file = f"{PATH_LIVE_ARTIFACTS}/worker_{loop_num}_{worker_id}_stdout.txt"
            with open(stdout_file, "w") as f:
                f.write(stdout_text + "\n--- STDERR ---\n" + stderr_text)

            # Read the summary file the worker created
            output_file = f"{PATH_LIVE_ARTIFACTS}/worker_{worker_id}_output.md"
            if os.path.exists(output_file):
                with open(output_file, "r") as f:
                    result = f.read()
                move_to_archive(output_file, PATH_ARCHIVED_ARTIFACTS)
                logging.info(f"  ✅ [WORKER {worker_id}] Finished. ({elapsed:.1f}s)")
            else:
                result = "Worker completed, but no output file was found."
                logging.warning(f"  ⚠️ [WORKER {worker_id}] Finished but output file missing. ({elapsed:.1f}s)")
            return f"--- WORKER {worker_id} REPORT ---\n{result}\n"

        finally:
            if master_fd != -1:
                try:
                    os.close(master_fd)
                except OSError:
                    pass
            await deregister_worker(worker_id)
