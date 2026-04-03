import asyncio
import subprocess
import json
import os
import glob
import logging
import sys
import time

from config import (
    PATH_PLANS,
    PATH_ARTIFACTS,
    PATH_ARCHIVED_MEMORY,
    PATH_ARCHIVED_ARTIFACTS,
    PATH_LOGS,
    N_SUB_AGENTS,
    N_MAX_LOOPS,
    MAX_TURNS,
    UNATTENDED_MODE,
    MODEL_ORCHESTRATOR,
    MODEL_UTILITY,
    PLANNING_MEMORY_FILE,
    PLANNING_STATE_FILE,
    RISK_ASSESSMENT_FILE,
    HUMAN_FEEDBACK_FILE,
    EXECUTION_STATE_FILE,
    EXECUTION_FEEDBACK_FILE,
)
from src.helpers import (
    CircuitBreakerOpenError,
    _stream_with_intercept,
    _get_baseline_commit,
    count_git_diff_files,
    count_new_artifacts,
    extract_error_signature,
    load_planning_state,
    save_planning_state,
    move_to_archive,
    load_execution_state,
    save_execution_state,
)
from src.safeguards import CircuitBreaker, ExitGate, RateLimiter
from src.safeguards.status_writer import (
    write_status,
    register_worker,
    deregister_worker,
    get_active_workers,
)

os.makedirs(PATH_PLANS, exist_ok=True)
os.makedirs(PATH_ARTIFACTS, exist_ok=True)
os.makedirs(PATH_LOGS, exist_ok=True)
os.makedirs(PATH_ARCHIVED_MEMORY, exist_ok=True)
os.makedirs(PATH_ARCHIVED_ARTIFACTS, exist_ok=True)

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
            When finished, write a brief summary of your findings/actions to a file named '{PATH_ARTIFACTS}/worker_{worker_id}_output.md' and exit.
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
            stdout_file = f"{PATH_ARTIFACTS}/worker_{loop_num}_{worker_id}_stdout.txt"
            with open(stdout_file, "w") as f:
                f.write(stdout_text + "\n--- STDERR ---\n" + stderr_text)

            # Read the summary file the worker created
            output_file = f"{PATH_ARTIFACTS}/worker_{worker_id}_output.md"
            result = "Worker completed, but no output file was found."
            if os.path.exists(output_file):
                with open(output_file, "r") as f:
                    result = f.read()
                move_to_archive(output_file, PATH_ARCHIVED_ARTIFACTS)

            logging.info(f"  ✅ [WORKER {worker_id}] Finished.")
            return f"--- WORKER {worker_id} REPORT ---\n{result}\n"

        finally:
            await deregister_worker(worker_id)


def run_orchestrator(
    prompt: str,
    require_json: bool = False,
    rate_limiter: RateLimiter | None = None,
    model: str = MODEL_ORCHESTRATOR,
) -> str | dict | None:
    """Runs the Master Orchestrator synchronously.

    If rate-limited, waits (time.sleep) until the cooldown expires, then
    proceeds.  This is already a blocking context (sync subprocess), so the
    sync sleep adds no new regression.

    Args:
        model: Claude model ID. Defaults to ``MODEL_ORCHESTRATOR`` from config.
               Pass ``MODEL_UTILITY`` for cheap tasks (memory updates, summaries).
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

    cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions", "--max-turns", MAX_TURNS, "--model", model]
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = process.communicate()

    # Check for rate-limit signals in orchestrator output
    if rate_limiter and rate_limiter.parse_output_for_limit(stdout, stderr):
        rate_limiter.record_rate_limit_signal()

    if require_json:
        import re
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
# PLAN REVIEW & REFINEMENT
# ==========================================

async def plan_refinement_phase():
    logging.info("\n" + "="*50)
    logging.info("🎯 PLAN REVIEW & REFINEMENT")
    logging.info("="*50)

    if not os.path.exists(f"{PATH_PLANS}/initial_plan.md"):
        logging.error("❌ Error: 'initial_plan.md' not found. Please create it first.")
        exit(1)

    # ── State detection ──────────────────────────────────────────────────────
    state = load_planning_state(PLANNING_STATE_FILE)
    resuming = state and state.get("status") == "awaiting_review"
    iteration = state.get("iteration", 1) if state else 1

    if resuming:
        logging.info(
            "↩️  Resuming — pending review found. "
            f"Reading {RISK_ASSESSMENT_FILE}."
        )
    else:
        # Fresh start: reset iteration counter and initialise memory file
        iteration = 1
        with open(PLANNING_MEMORY_FILE, "w") as f:
            f.write("# Planning Memory\n\nFresh planning run started.\n")
        save_planning_state(PLANNING_STATE_FILE, "in_progress", iteration)

    while True:
        if not resuming:
            # ── Step 1: Research delegation ──────────────────────────────────
            memory_context = ""
            if os.path.exists(PLANNING_MEMORY_FILE):
                with open(PLANNING_MEMORY_FILE, "r") as f:
                    memory_context = f.read()

            delegation_prompt = f"""
            Read '{PATH_PLANS}/initial_plan.md'. We need to refine this into a robust multi-phased plan.
            Use your expert judgement as a senior software engineer to breakdown the plan into as many phases as necessary.
            Crucially, every phase MUST have a set of KPIs (unit/integration tests, or specific verifiable tasks if tests aren't possible) to ensure functionality and integration with adjacent phases.

            Planning memory from prior iterations (do not repeat covered ground):
            {memory_context}

            Do you need independent agents to conduct research (e.g., checking library docs, exploring API limits, checking feasibility) before finalizing the plan?
            Output a JSON array of research tasks. If no research is needed, output an empty array[].
            Format: {{"research_tasks": ["task 1", "task 2"]}}
            """
            delegation_data = run_orchestrator(delegation_prompt, require_json=True)

            # ── Step 2: Research workers ──────────────────────────────────────
            if delegation_data and delegation_data.get("research_tasks"):
                tasks = delegation_data["research_tasks"]
                logging.info(f"\n🔍 Orchestrator delegated {len(tasks)} research tasks to workers.")
                sem = asyncio.Semaphore(N_SUB_AGENTS)
                worker_coroutines = [run_worker_agent(sem, i+1, task) for i, task in enumerate(tasks)]
                research_results = await asyncio.gather(*worker_coroutines)
                research_context = "\n".join(research_results)
            else:
                research_context = "No additional research was required."

            # ── Step 3: Plan generation ───────────────────────────────────────
            split_plan_prompt = f"""
            Here is the research gathered by the workers:
            {research_context}

            Based on '{PATH_PLANS}/initial_plan.md' and the research, create the finalized multi-phased plan.
            1. Break the plan down into separate files named exactly '{PATH_PLANS}/phase_1_plan.md', '{PATH_PLANS}/phase_2_plan.md', etc.
            2. In each file, explicitly list the KPIs (tests or verifiable tasks) required to complete the phase.
            3. Write a summary of the overall architecture to '{PATH_PLANS}/architecture_summary.md'.
            Use your file writing tools to create these files now.
            """
            run_orchestrator(split_plan_prompt,model=MODEL_ORCHESTRATOR)

            # ── Step 3.5: Risk assessment ────────────────────────────────────
            clarification_prompt = f"""
            You have just drafted the phase plans and architecture summary in the {PATH_PLANS} directory.
            Before we proceed to execution, act as a strict Senior Staff Engineer.
            Review the plans you just created. Are there any missing links, ambiguous requirements, potential security flaws, or oversights?

            Write a brief 'Risk Assessment & Clarifications' report addressed to the human supervisor.
            Explicitly list any questions you need the human to answer or clarify before we can safely proceed to coding.
            Also write this exact report to the file '{RISK_ASSESSMENT_FILE}' using your file tools.
            """
            clarification_report = run_orchestrator(clarification_prompt)

            # Persist risk assessment to file (in case the orchestrator didn't write it)
            if clarification_report:
                with open(RISK_ASSESSMENT_FILE, "w") as f:
                    f.write(clarification_report)

            # ── Update planning memory ────────────────────────────────────────
            update_memory_prompt = (
                f"Append a concise summary of iteration {iteration} to '{PLANNING_MEMORY_FILE}'. "
                f"Include: research tasks delegated, key findings from the research, "
                f"and the major decisions made in the plan files. "
                f"As much as possible, keep the entry under 300 words so the file stays scannable."
            )
            run_orchestrator(update_memory_prompt, model=MODEL_UTILITY)

            # Save state as awaiting_review before blocking on HITL
            save_planning_state(PLANNING_STATE_FILE, "awaiting_review", iteration)

        # ── Display risk assessment ───────────────────────────────────────────
        logging.info("\n" + "="*50)
        logging.info("🧐 ORCHESTRATOR RISK ASSESSMENT & CLARIFICATIONS")
        logging.info("="*50)

        report_text = ""
        if os.path.exists(RISK_ASSESSMENT_FILE):
            with open(RISK_ASSESSMENT_FILE, "r") as f:
                report_text = f.read()
        print(f"\n{report_text}\n")

        # ── HITL ─────────────────────────────────────────────────────────────
        logging.info("\n" + "="*50)
        logging.info("👨‍💻 HUMAN REVIEW REQUIRED")
        logging.info(f"Phase plans are in {PATH_PLANS}/")
        logging.info(f"Risk assessment: {RISK_ASSESSMENT_FILE}")
        logging.info(f"Break-off: type 'wait' (or Ctrl-C) to exit and write feedback to {HUMAN_FEEDBACK_FILE}")

        # Check for pre-written feedback file
        user_input = None
        if os.path.exists(HUMAN_FEEDBACK_FILE):
            with open(HUMAN_FEEDBACK_FILE, "r") as f:
                file_feedback = f.read().strip()
            if file_feedback:
                logging.info(f"📄 Found feedback in {HUMAN_FEEDBACK_FILE} — using it.")
                user_input = file_feedback
                move_to_archive(HUMAN_FEEDBACK_FILE, PATH_ARCHIVED_ARTIFACTS)

        if user_input is None:
            print(
                f"\nType 'approve' to begin execution, 'wait' to exit and write feedback to "
                f"'{HUMAN_FEEDBACK_FILE}', or enter your answers inline:"
            )
            try:
                user_input = input(">>> ").strip()
            except KeyboardInterrupt:
                user_input = "wait"

        # Handle break-off
        if user_input.lower() == "wait":
            logging.info(
                f"⏸️  Break-off requested. State saved. "
                f"Write feedback to '{HUMAN_FEEDBACK_FILE}' and re-run to resume."
            )
            save_planning_state(PLANNING_STATE_FILE, "awaiting_review", iteration)
            sys.exit(0)

        # Handle approval
        if user_input.lower() in ['approve', 'yes', 'y']:
            logging.info("✅ Plan approved. Moving to Execution Phase.")
            # Archive planning memory and clear state
            memory_archive_path = f"{PATH_ARCHIVED_MEMORY}/planning_phase_memory.md"
            if os.path.exists(PLANNING_MEMORY_FILE):
                os.replace(PLANNING_MEMORY_FILE, memory_archive_path)
                logging.info(f"📦 Planning memory archived to {memory_archive_path}")
            # Archive transient planning artifacts
            move_to_archive(RISK_ASSESSMENT_FILE, PATH_ARCHIVED_ARTIFACTS)
            move_to_archive(HUMAN_FEEDBACK_FILE, PATH_ARCHIVED_ARTIFACTS)
            break

        # ── Feedback loop ─────────────────────────────────────────────────────
        logging.info("🔄 Sending human feedback back to Orchestrator to update plans...")

        # Update memory with human feedback
        with open(PLANNING_MEMORY_FILE, "a") as f:
            f.write(f"- Human feedback (iteration {iteration}): {user_input[:500]}\n")

        fix_prompt = f"""
        The human supervisor provided the following answers and feedback to your questions:
        '{user_input}'

        Based on this new information, use your file editing tools to update the relevant phase_X_plan.md and architecture_summary.md files in the {PATH_PLANS} directory.
        """
        run_orchestrator(fix_prompt)

        iteration += 1
        resuming = False  # next loop runs full research + plan cycle
        save_planning_state(PLANNING_STATE_FILE, "in_progress", iteration)


# ==========================================
# PLAN EXECUTION
# ==========================================

async def execution_phase():
    logging.info("\n" + "="*50)
    logging.info("🚀 PLAN EXECUTION")
    logging.info("="*50)

    # Instantiate safeguards (shared across all phases in this run)
    rate_limiter = RateLimiter()
    circuit_breaker = CircuitBreaker()
    exit_gate = ExitGate()

    ex_state = load_execution_state(EXECUTION_STATE_FILE)
    completed_phases: list[str] = ex_state.get("completed_phases", []) if ex_state else []
    if ex_state:
        logging.info(
            f"↩️  Resuming execution. Completed phases: {completed_phases}. "
            f"Last active phase: {ex_state.get('current_phase')}."
        )

    # Find all phase files generated in Phase 0
    phase_files = sorted(glob.glob(f"{PATH_PLANS}/phase_*_plan.md"))
    if not phase_files:
        logging.error("❌ No phase plan files found!")
        return

    for phase_file in phase_files:
        phase_name = os.path.basename(phase_file).replace('_plan.md', '')
        memory_file = f"{PATH_ARTIFACTS}/{phase_name}_memory.md"

        if phase_name in completed_phases:
            logging.info(f"⏭️  Skipping {phase_name} (already completed).")
            continue

        # Reset exit gate between phases so signals from prior phases don't bleed in
        exit_gate.reset()

        # Initialize Memory File (preserve on resume)
        if not os.path.exists(memory_file):
            with open(memory_file, "w") as f:
                f.write(f"# Memory for {phase_name}\nExecution started.\n")
        else:
            logging.info(f"↩️  Resuming {phase_name}: existing memory file preserved.")

        save_execution_state(EXECUTION_STATE_FILE, completed_phases, phase_name, 1)

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

            # 2. Execute Tasks via Workers (Bounded by N_SUB_AGENTS)
            all_worker_outputs: list[str] = []
            if tasks:
                logging.info(f"🛠️ Orchestrator delegated {len(tasks)} execution tasks.")
                sem = asyncio.Semaphore(N_SUB_AGENTS)
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
            run_orchestrator(update_memory_prompt, rate_limiter=rate_limiter, model=MODEL_UTILITY)

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
            save_execution_state(EXECUTION_STATE_FILE, completed_phases, phase_name, loop_num)

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
                    user_input = None
                    if os.path.exists(EXECUTION_FEEDBACK_FILE):
                        with open(EXECUTION_FEEDBACK_FILE, "r") as f:
                            file_feedback = f.read().strip()
                        if file_feedback:
                            logging.info(f"📄 Found feedback in {EXECUTION_FEEDBACK_FILE} — using it.")
                            user_input = file_feedback
                            move_to_archive(EXECUTION_FEEDBACK_FILE, PATH_ARCHIVED_ARTIFACTS)

                    if user_input is None:
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
        move_to_archive(memory_file, PATH_ARCHIVED_MEMORY)
        completed_phases.append(phase_name)
        save_execution_state(EXECUTION_STATE_FILE, completed_phases, phase_name, loop_num)

    logging.info("\n🎉 ALL PHASES COMPLETED SUCCESSFULLY! 🎉")


# ==========================================
# MAIN ENTRY POINT
# ==========================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Claude Autonomous Harness")
    parser.add_argument(
        "--mode",
        choices=["planning", "execution", "full"],
        default="full",
        help="Which phases to run (default: full)",
    )
    parser.add_argument(
        "--sub-agents",
        type=int,
        default=N_SUB_AGENTS,
        metavar="N",
        help=f"Max concurrent worker agents (default: {N_SUB_AGENTS})",
    )
    parser.add_argument(
        "--max-loops",
        type=int,
        default=N_MAX_LOOPS,
        metavar="N",
        help=f"Max execution loops per phase (default: {N_MAX_LOOPS})",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=int(MAX_TURNS),
        metavar="N",
        help=f"Max autonomous tool turns per Claude session (default: {MAX_TURNS})",
    )
    parser.add_argument(
        "--unattended",
        action="store_true",
        default=UNATTENDED_MODE,
        help="Skip HITL prompts and auto-wait on rate-limit events",
    )
    args = parser.parse_args()

    # Override module-level names so all functions pick up CLI values
    N_SUB_AGENTS    = args.sub_agents
    N_MAX_LOOPS     = args.max_loops
    MAX_TURNS       = str(args.max_turns)
    UNATTENDED_MODE = args.unattended

    async def main():
        if args.mode == "planning":
            logging.info("📋 Planning Review.")
            await plan_refinement_phase()
        elif args.mode == "execution":
            logging.info("⏭️ Plan Execution.")
            await execution_phase()
        else:  # "full" or default
            logging.info("🚀 Full Run: Planning + Execution.")
            await plan_refinement_phase()
            await execution_phase()

    asyncio.run(main())
