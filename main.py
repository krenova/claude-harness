import asyncio
import subprocess
import json
import re
import os
import glob
import logging
import sys
import time

from src.safeguards import CircuitBreaker, ExitGate, RateLimiter
from src.safeguards.status_writer import (
    write_status,
    register_worker,
    deregister_worker,
    get_active_workers,
)

PATH_AMA_PLANS = "./plans"
PATH_AMA_ARTIFACTS = "./.artifacts"
PATH_LOGS = "./.logs"

os.makedirs(PATH_AMA_PLANS, exist_ok=True)
os.makedirs(PATH_AMA_ARTIFACTS, exist_ok=True)
os.makedirs(PATH_LOGS, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(module)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    filename=f'{PATH_LOGS}/orchestrator.log',
    filemode='a'
)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(module)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logging.getLogger().addHandler(console_handler)

# ==========================================
# CONFIGURATION
# ==========================================
X1_MAX_WORKERS = 3  # Maximum number of independent agents running at once
N_MAX_LOOPS = 5     # Maximum execution loops per phase before forcing a halt
MAX_TURNS = "15"    # Max autonomous tool loops Claude can take per session

# AMA_UNATTENDED=1 skips HITL prompts in the execution phase and auto-waits
# on rate-limit / circuit-breaker events.  Planning phase always requires
# human approval regardless of this flag.
UNATTENDED_MODE = os.environ.get("AMA_UNATTENDED", "0") == "1"

# ==========================================
# EXCEPTIONS
# ==========================================

class CircuitBreakerOpenError(Exception):
    """Raised when the circuit breaker opens and UNATTENDED_MODE is False."""


# ==========================================
# INTERACTIVE PROMPT INTERCEPT
# ==========================================

INTERACTIVE_PROMPT_PATTERNS = [
    re.compile(r"\(yes/no(/\[fingerprint\])?\)\s*\??$", re.IGNORECASE),
    re.compile(r"password\s*:", re.IGNORECASE),
    re.compile(r"enter passphrase", re.IGNORECASE),
    re.compile(r"please type 'yes', 'no' or the fingerprint", re.IGNORECASE),
]

# Default answers injected when a matching prompt is detected in UNATTENDED_MODE.
# One entry per pattern above (index-aligned).
_UNATTENDED_DEFAULTS = ["yes", "yes", "yes", "yes"]


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
                            _UNATTENDED_DEFAULTS[idx]
                            if idx < len(_UNATTENDED_DEFAULTS)
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
    pattern = f"{PATH_AMA_ARTIFACTS}/worker_{loop_num}_*_stdout.txt"
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
# CORE AGENT RUNNERS
# ==========================================

async def run_worker_agent(
    sem,
    worker_id: int,
    task_prompt: str,
    loop_num: int = 0,
    rate_limiter: RateLimiter | None = None,
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
            logging.info(f"  🚀 [WORKER {worker_id}] Starting task...")
            full_prompt = f"""
            You are Independent Worker {worker_id}.
            Execute the following task using your tools. Do not ask for human input. If you are not able to execute the task, do your best to get as much done as possible and explain what you couldn't complete and why.
            When finished, write a brief summary of your findings/actions to a file named '{PATH_AMA_ARTIFACTS}/worker_{worker_id}_output.md' and exit.
            TASK: {task_prompt}
            """

            process = await asyncio.create_subprocess_exec(
                "claude", "-p", full_prompt, "--dangerously-skip-permissions",
                "--max-turns", MAX_TURNS,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_text, stderr_text = await _stream_with_intercept(process, worker_id)

            # Check for rate-limit signals in subprocess output
            if rate_limiter and rate_limiter.parse_output_for_limit(stdout_text, stderr_text):
                rate_limiter.record_rate_limit_signal()

            # Persist raw stdout/stderr for debugging and circuit-breaker analysis.
            # Including loop_num prevents per-loop overwrites (full history for post-mortem).
            stdout_file = f"{PATH_AMA_ARTIFACTS}/worker_{loop_num}_{worker_id}_stdout.txt"
            with open(stdout_file, "w") as f:
                f.write(stdout_text + "\n--- STDERR ---\n" + stderr_text)

            # Read the summary file the worker created
            output_file = f"{PATH_AMA_ARTIFACTS}/worker_{worker_id}_output.md"
            result = "Worker completed, but no output file was found."
            if os.path.exists(output_file):
                with open(output_file, "r") as f:
                    result = f.read()
                os.remove(output_file)

            logging.info(f"  ✅ [WORKER {worker_id}] Finished.")
            return f"--- WORKER {worker_id} REPORT ---\n{result}\n"

        finally:
            await deregister_worker(worker_id)


def run_orchestrator(
    prompt: str,
    require_json: bool = False,
    rate_limiter: RateLimiter | None = None,
) -> str | dict | None:
    """Runs the Master Orchestrator synchronously.

    If rate-limited, waits (time.sleep) until the cooldown expires, then
    proceeds.  This is already a blocking context (sync subprocess), so the
    sync sleep adds no new regression.
    """
    logging.info(f"\n🧠 [ORCHESTRATOR] Thinking...")

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

    process = subprocess.Popen(
        ["claude", "-p", prompt, "--dangerously-skip-permissions", "--max-turns", MAX_TURNS],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = process.communicate()

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
        return None

    return stdout


# ==========================================
# PHASE 0: PLAN REVIEW & REFINEMENT
# ==========================================

async def plan_refinement_phase():
    logging.info("\n" + "="*50)
    logging.info("🎯 PHASE 0: MULTI-PHASED PLAN REVIEW")
    logging.info("="*50)

    if not os.path.exists(f"{PATH_AMA_PLANS}/initial_plan.md"):
        logging.error("❌ Error: 'initial_plan.md' not found. Please create it first.")
        exit(1)

    while True:
        # 1. Orchestrator reviews the plan and decides if research is needed
        delegation_prompt = f"""
        Read 'initial_plan.md'. We need to refine this into a robust multi-phased plan.
        Crucially, every phase MUST have a set of KPIs (unit/integration tests, or specific verifiable tasks if tests aren't possible) to ensure functionality and integration with adjacent phases.

        Do you need independent agents to conduct research (e.g., checking library docs, exploring API limits, checking feasibility) before finalizing the plan?
        Output a JSON array of research tasks. If no research is needed, output an empty array[].
        Format: {{"research_tasks": ["task 1", "task 2"]}}
        """
        delegation_data = run_orchestrator(delegation_prompt, require_json=True)

        # 2. Spin up workers if research is needed
        if delegation_data and delegation_data.get("research_tasks"):
            tasks = delegation_data["research_tasks"]
            logging.info(f"\n🔍 Orchestrator delegated {len(tasks)} research tasks to workers.")
            sem = asyncio.Semaphore(X1_MAX_WORKERS)
            worker_coroutines = [run_worker_agent(sem, i+1, task) for i, task in enumerate(tasks)]
            research_results = await asyncio.gather(*worker_coroutines)
            research_context = "\n".join(research_results)
        else:
            research_context = "No additional research was required."

        # 3. Orchestrator finalizes the plan and splits it into files
        split_plan_prompt = f"""
        Here is the research gathered by the workers:
        {research_context}

        Based on 'initial_plan.md' and the research, create the finalized multi-phased plan.
        1. Break the plan down into separate files named exactly '{PATH_AMA_PLANS}/phase_1_plan.md', '{PATH_AMA_PLANS}/phase_2_plan.md', etc.
        2. In each file, explicitly list the KPIs (tests or verifiable tasks) required to complete the phase.
        3. Write a summary of the overall architecture to '{PATH_AMA_PLANS}/architecture_summary.md'.
        Use your file writing tools to create these files now.
        """
        run_orchestrator(split_plan_prompt)

        # 3.5. Orchestrator seeks clarifications
        clarification_prompt = f"""
        You have just drafted the phase plans and architecture summary in the {PATH_AMA_PLANS} directory.
        Before we proceed to execution, act as a strict Senior Staff Engineer.
        Review the plans you just created. Are there any missing links, ambiguous requirements, potential security flaws, or oversights?

        Write a brief 'Risk Assessment & Clarifications' report addressed to the human supervisor.
        Explicitly list any questions you need the human to answer or clarify before we can safely proceed to coding.
        """
        clarification_report = run_orchestrator(clarification_prompt)

        logging.info("\n" + "="*50)
        logging.info("🧐 ORCHESTRATOR RISK ASSESSMENT & CLARIFICATIONS")
        logging.info("="*50)
        print(f"\n{clarification_report}\n")

        # 4. Human-In-The-Loop (HITL) — always required; UNATTENDED_MODE has no effect here
        logging.info("\n" + "="*50)
        logging.info("👨‍💻 HUMAN REVIEW REQUIRED")
        logging.info(f"The Orchestrator has generated the phase plan files in {PATH_AMA_PLANS}/.")

        user_input = input("Type 'approve' to begin execution, OR type your answers to the Orchestrator's questions/feedback: ")

        if user_input.lower() in ['approve', 'yes', 'y']:
            logging.info("✅ Plan approved. Moving to Execution Phase.")
            break
        else:
            logging.info("🔄 Sending human feedback back to Orchestrator to update plans...")
            fix_prompt = f"""
            The human supervisor provided the following answers and feedback to your questions:
            '{user_input}'

            Based on this new information, use your file editing tools to update the relevant phase_X_plan.md and architecture_summary.md files in the {PATH_AMA_PLANS} directory.
            """
            run_orchestrator(fix_prompt)


# ==========================================
# PHASE 2: EXECUTION LOOPS
# ==========================================

async def execution_phase():
    logging.info("\n" + "="*50)
    logging.info("🚀 PHASE 2: PLAN EXECUTION")
    logging.info("="*50)

    # Instantiate safeguards (shared across all phases in this run)
    rate_limiter = RateLimiter()
    circuit_breaker = CircuitBreaker()
    exit_gate = ExitGate()

    # Find all phase files generated in Phase 0
    phase_files = sorted(glob.glob(f"{PATH_AMA_PLANS}/phase_*_plan.md"))
    if not phase_files:
        logging.error("❌ No phase plan files found!")
        return

    for phase_file in phase_files:
        phase_name = os.path.basename(phase_file).replace('_plan.md', '')
        memory_file = f"{PATH_AMA_ARTIFACTS}/{phase_name}_memory.md"

        # Reset exit gate between phases so signals from prior phases don't bleed in
        exit_gate.reset()

        # Initialize Memory File
        with open(memory_file, "w") as f:
            f.write(f"# Memory for {phase_name}\nExecution started.\n")

        logging.info(f"\n" + "-"*40)
        logging.info(f"⚙️ COMMENCING: {phase_name.upper()}")
        logging.info("-"*40)

        # Use a while-loop (not for-loop) so loop_num can be held constant
        # during a circuit-breaker cooldown without consuming the loop budget.
        loop_num = 1
        while loop_num <= N_MAX_LOOPS:
            logging.info(f"\n🔄 [LOOP {loop_num}/{N_MAX_LOOPS}] Planning execution...")

            # Capture baseline commit BEFORE any work so diff is loop-scoped
            baseline_commit = _get_baseline_commit()

            # 1. Orchestrator plans tasks for workers
            task_prompt = f"""
            We are executing {phase_file}.
            Read {phase_file} and {memory_file}. Look at the current codebase state.
            What tasks need to be executed right now by the worker agents to progress this phase and meet the KPIs?
            Output a JSON object containing a list of tasks. If the phase is completely finished and all KPIs are met, output an empty list.
            Format: {{"tasks":["write backend tests", "implement login UI"]}}
            """
            task_data = run_orchestrator(task_prompt, require_json=True, rate_limiter=rate_limiter)
            tasks = task_data.get("tasks", []) if task_data else []

            # 2. Execute Tasks via Workers (Bounded by X1_MAX_WORKERS)
            all_worker_outputs: list[str] = []
            if tasks:
                logging.info(f"🛠️ Orchestrator delegated {len(tasks)} execution tasks.")
                sem = asyncio.Semaphore(X1_MAX_WORKERS)
                worker_coroutines = [
                    run_worker_agent(
                        sem, i + 1, task,
                        loop_num=loop_num,
                        rate_limiter=rate_limiter,
                    )
                    for i, task in enumerate(tasks)
                ]
                all_worker_outputs = list(await asyncio.gather(*worker_coroutines))
            else:
                logging.info("No tasks delegated. Orchestrator believes phase might be complete.")

            # 3. Orchestrator Reviews Work against KPIs
            review_prompt = f"""
            The workers have finished their tasks.
            Review the current codebase against the KPIs defined in {phase_file}.
            Run any necessary unit/integration tests using your bash tools.

            Are all KPIs met? Are there bugs? Do we need to add new KPIs based on new findings?
            Output a JSON object:
            {{
                "kpis_met": true/false,
                "any_new_kpi_satisfied": true/false,
                "summary": "Brief summary of what works and what is broken",
                "proposed_fixes_or_new_kpis": "What needs to happen next loop, if anything"
            }}
            """
            review_data = run_orchestrator(review_prompt, require_json=True, rate_limiter=rate_limiter)

            # 4. Update Memory
            update_memory_prompt = (
                f"Update {memory_file} with a summary of Loop {loop_num}: "
                "what was done, what failed, and current KPI status."
            )
            run_orchestrator(update_memory_prompt, rate_limiter=rate_limiter)

            # 5. Update Exit Gate
            exit_gate.record_worker_outputs(all_worker_outputs)
            exit_gate.record_kpi_review(
                kpis_met=review_data.get("kpis_met", False) if review_data else False
            )

            # 6. Update Circuit Breaker
            files_changed = count_git_diff_files(baseline_commit)
            artifacts_produced = count_new_artifacts(loop_num)
            kpi_advancement = (
                review_data.get("any_new_kpi_satisfied", False) if review_data else False
            )
            error_sig = extract_error_signature(all_worker_outputs)
            circuit_breaker.record_loop_result(
                files_changed=files_changed,
                worker_artifacts_produced=artifacts_produced,
                kpi_advancement=kpi_advancement,
                error_signature=error_sig,
            )

            # 7. Write status.json (enables monitoring and crash recovery)
            gate_state = exit_gate.get_state()
            write_status(
                phase=phase_name,
                loop_count=loop_num,
                api_calls_this_hour=rate_limiter.api_calls_this_hour,
                circuit_breaker_state=circuit_breaker.get_state(),
                exit_gate_heuristic=gate_state.heuristic_score,
                exit_gate_kpis_met=gate_state.kpis_met_confirmed,
                active_workers=get_active_workers(),
                rate_limit_cooldown_until=rate_limiter.rate_limit_cooldown_until,
            )

            if review_data:
                logging.info(
                    f"\n📊 ORCHESTRATOR REVIEW:\n"
                    f"- KPIs Met: {review_data.get('kpis_met')}\n"
                    f"- Summary: {review_data.get('summary')}"
                )

            # 8. Circuit Breaker check — must come before exit-gate so a stuck loop
            #    doesn't accidentally trigger the safety-breaker path in ExitGate.
            if circuit_breaker.is_open():
                logging.error("⚡ Circuit breaker OPEN — pausing execution.")
                if UNATTENDED_MODE:
                    logging.warning(
                        f"⏳ CB cooldown sleeping {circuit_breaker.cooldown_seconds}s. "
                        "Loop counter paused."
                    )
                    await asyncio.sleep(circuit_breaker.cooldown_seconds)
                    circuit_breaker.check_cooldown()
                    continue  # loop_num NOT incremented — counter paused during cooldown
                else:
                    raise CircuitBreakerOpenError("Manual intervention required.")

            # 9. Exit Gate check — replaces the old bare `if kpis_met: break`
            if exit_gate.should_exit():
                logging.info("✅ Exit gate opened — phase complete.")
                break

            # 10. HITL (skipped when UNATTENDED_MODE=1)
            if not UNATTENDED_MODE:
                if review_data and not review_data.get("kpis_met"):
                    logging.info(
                        f"\n⚠️ KPIs not met. Proposed fixes: "
                        f"{review_data.get('proposed_fixes_or_new_kpis')}"
                    )
                    user_input = input(
                        "👨‍💻 HUMAN INPUT: Type 'continue' to let the AMA fix this "
                        "in the next loop, or provide specific guidance/new KPIs: "
                    )
                    if user_input.lower() not in ['continue', 'c', 'yes', 'y']:
                        with open(memory_file, "a") as f:
                            f.write(f"\nHuman Feedback for next loop: {user_input}\n")

            loop_num += 1

        else:
            logging.info(
                f"\n⚠️ Reached maximum loops ({N_MAX_LOOPS}) for {phase_name}. "
                "Forcing progression."
            )

        # Phase Completion Report
        report_prompt = f"""
        We have finished {phase_name}.
        Read {memory_file} and the codebase.
        Draft a comprehensive markdown report named '{phase_name}_report.md' summarizing the work completed, the KPIs achieved, and any technical debt left over.
        """
        run_orchestrator(report_prompt, rate_limiter=rate_limiter)
        logging.info(f"📝 Generated {phase_name}_report.md")

    logging.info("\n🎉 ALL PHASES COMPLETED SUCCESSFULLY! 🎉")


# ==========================================
# MAIN ENTRY POINT
# ==========================================
if __name__ == "__main__":
    async def main():
        if "--skip-planning" in sys.argv:
            logging.info("⏭️ Skipping Phase 0: Plan Review (via command line flag).")
        else:
            await plan_refinement_phase()

        await execution_phase()

    asyncio.run(main())
