import asyncio
import logging
import subprocess
import time

from config import MODEL_ORCHESTRATOR, MAX_TURNS
from src.safeguards import RateLimiter

_OVERRIDE_KEYWORD = "r"   # user must type this (case-insensitive) + Enter to override


async def _wait_for_cooldown_async(wait_secs: float) -> bool:
    """Async: block until cooldown expires or user types 'r' + Enter to override.

    Returns True if the user overrode the cooldown, False if it expired normally.
    """
    override_event = asyncio.Event()

    def _do_input() -> str:
        try:
            return input()
        except (EOFError, OSError):
            return ""

    async def _listen() -> None:
        loop = asyncio.get_running_loop()
        while not override_event.is_set():
            line = await loop.run_in_executor(None, _do_input)
            if line.strip().lower() == _OVERRIDE_KEYWORD:
                override_event.set()

    listener_task = asyncio.create_task(_listen())
    print(
        f"\n⏳ Cooldown: {wait_secs:.0f}s remaining. "
        f"Type '{_OVERRIDE_KEYWORD}' + ENTER to override and continue immediately.\n"
    )

    remaining = wait_secs
    while remaining > 0 and not override_event.is_set():
        await asyncio.sleep(min(1, remaining))  # interruptible!
        remaining -= 1
        if remaining > 0 and remaining % 60 == 0:
            print(f"⏳ Cooldown: {remaining:.0f}s remaining. Type '{_OVERRIDE_KEYWORD}' + ENTER to override.\n")

    listener_task.cancel()
    try:
        await listener_task
    except asyncio.CancelledError:
        pass

    return override_event.is_set()


def _sync_orchestrator(
    prompt: str,
    rate_limiter: RateLimiter | None = None,
    model: str = MODEL_ORCHESTRATOR,
    max_turns: str = MAX_TURNS,
    output_format: str | None = None,
    json_schema: str | None = None,
) -> str | None:
    """Runs the Master Orchestrator synchronously (must run in thread executor)."""
    logging.info("🧠 [ORCHESTRATOR] Thinking...")
    t0 = time.time()

    if rate_limiter:
        rate_limiter.record_call()

    cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions", "--sandbox", "--max-turns", max_turns, "--model", model]
    if output_format:
        cmd += ["--output-format", output_format]
    if json_schema:
        cmd += ["--json-schema", json_schema]
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = process.communicate()
    elapsed = time.time() - t0
    logging.info(f"🧠 [ORCHESTRATOR] Done. ({elapsed:.1f}s)")

    if process.returncode != 0:
        logging.warning(f"🧠 [ORCHESTRATOR] Exited with code {process.returncode} (possible max-turns limit hit).")

    if stderr.strip():
        logging.warning(f"🧠 [ORCHESTRATOR] stderr: {stderr.strip()[:300]}")

    # Check for rate-limit signals in orchestrator output
    if rate_limiter and rate_limiter.parse_output_for_limit(stdout, stderr):
        rate_limiter.record_rate_limit_signal()

    if not stdout.strip():
        logging.warning("🧠 [ORCHESTRATOR] Returned empty output.")
    return stdout


async def run_orchestrator_async(
    prompt: str,
    rate_limiter: RateLimiter | None = None,
    model: str = MODEL_ORCHESTRATOR,
    max_turns: str = MAX_TURNS,
    output_format: str | None = None,
    json_schema: str | None = None,
) -> str | None:
    """Async wrapper: handles rate limiting asynchronously, then runs sync logic in a thread."""
    if rate_limiter and not rate_limiter.can_make_call():
        wait_secs = rate_limiter.seconds_until_reset()
        await _wait_for_cooldown_async(wait_secs)
        rate_limiter.clear_cooldown()

    return await asyncio.to_thread(
        _sync_orchestrator, prompt,
        rate_limiter=rate_limiter,
        model=model,
        max_turns=max_turns,
        output_format=output_format,
        json_schema=json_schema,
    )
