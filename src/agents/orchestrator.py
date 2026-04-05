import asyncio
import json
import logging
import re
import subprocess
import threading
import time

from config import MODEL_ORCHESTRATOR, MAX_TURNS
from src.safeguards import RateLimiter

_OVERRIDE_KEYWORD = "r"   # user must type this (case-insensitive) + Enter to override


def _wait_for_rate_limit(rate_limiter: RateLimiter, wait_secs: float) -> None:
    """Block until cooldown expires or user types 'r' + Enter to override."""
    override_event = threading.Event()

    def _listen() -> None:
        try:
            while not override_event.is_set():
                line = input()
                if line.strip().lower() == _OVERRIDE_KEYWORD:
                    override_event.set()
                    break
        except (EOFError, OSError):
            pass

    threading.Thread(target=_listen, daemon=True).start()
    print(
        f"\n⏳ Rate limit cooldown: {wait_secs:.0f}s remaining. "
        f"Type '{_OVERRIDE_KEYWORD}' + ENTER to override and retry immediately.\n"
    )

    elapsed = 0
    while elapsed < int(wait_secs) and not override_event.is_set():
        time.sleep(1)
        elapsed += 1
        if elapsed % 60 == 0 and not override_event.is_set():
            remaining = wait_secs - elapsed
            print(f"⏳ Cooldown: {remaining:.0f}s remaining. Type '{_OVERRIDE_KEYWORD}' + ENTER to override.\n")

    if override_event.is_set():
        logging.info("🔓 [ORCHESTRATOR] Cooldown overridden by user (typed '%s').", _OVERRIDE_KEYWORD)
    rate_limiter.clear_cooldown()


def run_orchestrator(
    prompt: str,
    require_json: bool = False,
    rate_limiter: RateLimiter | None = None,
    model: str = MODEL_ORCHESTRATOR,
    max_turns: str = MAX_TURNS,
) -> str | dict | None:
    """Runs the Master Orchestrator synchronously.

    If rate-limited, waits (time.sleep) until the cooldown expires, then
    proceeds.  This is already a blocking context (sync subprocess), so the
    sync sleep adds no new regression.

    Args:
        model: Claude model ID. Defaults to ``MODEL_ORCHESTRATOR`` from config.
               Pass ``MODEL_UTILITY`` for cheap tasks (memory updates, summaries).
        max_turns: Max autonomous tool turns per Claude session.
    """
    logging.info("\n🧠 [ORCHESTRATOR] Thinking...")
    t0 = time.time()

    if rate_limiter and not rate_limiter.can_make_call():
        wait_secs = rate_limiter.seconds_until_reset()
        _wait_for_rate_limit(rate_limiter, wait_secs)

    if rate_limiter:
        rate_limiter.record_call()

    if require_json:
        prompt += (
            "\n\nIMPORTANT: You MUST output your response ONLY as a valid JSON block "
            "enclosed in ```json ... ``` tags. Do not include conversational text outside "
            "the JSON block."
        )

    cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions", "--max-turns", max_turns, "--model", model]
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = process.communicate()
    elapsed = time.time() - t0
    logging.info(f"🧠 [ORCHESTRATOR] Done. ({elapsed:.1f}s)")

    if stderr.strip():
        logging.warning(f"🧠 [ORCHESTRATOR] stderr: {stderr.strip()[:300]}")

    # Check for rate-limit signals in orchestrator output
    if rate_limiter and rate_limiter.parse_output_for_limit(stdout, stderr):
        rate_limiter.record_rate_limit_signal()

    if require_json:
        match = re.search(r'```json\s*(.*?)\s*```', stdout, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                logging.error("❌ Failed to parse Orchestrator JSON.")
                return None
        logging.warning("🧠 [ORCHESTRATOR] No JSON block found in output.")
        return None

    if not stdout.strip():
        logging.warning("🧠 [ORCHESTRATOR] Returned empty output.")
    return stdout


async def run_orchestrator_async(
    prompt: str,
    require_json: bool = False,
    rate_limiter: RateLimiter | None = None,
    model: str = MODEL_ORCHESTRATOR,
    max_turns: str = MAX_TURNS,
) -> str | dict | None:
    """Async wrapper: runs run_orchestrator in a thread so the event loop stays free."""
    return await asyncio.to_thread(
        run_orchestrator, prompt,
        require_json=require_json,
        rate_limiter=rate_limiter,
        model=model,
        max_turns=max_turns,
    )
