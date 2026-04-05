import asyncio
import json
import logging
import re
import subprocess
import time

from config import MODEL_ORCHESTRATOR, MAX_TURNS
from src.safeguards import RateLimiter


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
        logging.warning(
            f"🚦 [ORCHESTRATOR] Rate limit reached. Waiting {wait_secs:.0f}s until reset. "
            "Progress is paused — this is expected, not an error."
        )
        print(f"\n⏳ Rate limit reached. Orchestrator sleeping {wait_secs:.0f}s...\n")
        time.sleep(wait_secs)
        rate_limiter.clear_cooldown()

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
