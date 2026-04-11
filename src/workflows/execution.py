import asyncio
import glob
import json as _json
import logging
import os
from pathlib import Path

from config import (
    PATH_PLANS,
    PATH_ARTIFACTS,
    PATH_LIVE_ARTIFACTS,
    PATH_ARCHIVED_MEMORY,
    PATH_ARCHIVED_ARTIFACTS,
    MODEL_UTILITY,
    EXECUTION_STATE_FILE,
    EXECUTION_FEEDBACK_FILE,
    RuntimeConfig,
)
from src.agents.orchestrator import run_orchestrator_async, _wait_for_cooldown_async
from src.agents.worker import run_worker_agent
from src.helpers import (
    _get_baseline_commit,
    clean_transient_artifacts,
    count_git_diff_files,
    count_new_artifacts,
    extract_error_signature,
    load_execution_state,
    save_execution_state,
    move_to_archive,
)
from src.safeguards import CircuitBreaker, ExitGate, ExitGateState, RateLimiter
from src.safeguards.status_writer import write_status, get_active_workers
from src.prompts.loader import load_prompt

_EXEC_PROMPTS = Path(__file__).parent.parent / "prompts" / "workflows" / "execution.yaml"
_STATUS_FILE = Path(f"{PATH_LIVE_ARTIFACTS}/status.json")

_REVIEW_SCHEMA = _json.dumps({
    "type": "object",
    "properties": {
        "kpis_met": {"type": "boolean"},
        "any_new_kpi_satisfied": {"type": "boolean"},
        "summary": {"type": "string"},
        "proposed_fixes_or_new_kpis": {"type": "string"},
    },
    "required": ["kpis_met", "any_new_kpi_satisfied", "summary", "proposed_fixes_or_new_kpis"],
})


def _load_status_json() -> dict | None:
    """Read .artifacts/status.json and return parsed dict, or None if absent/corrupt."""
    try:
        return _json.loads(_STATUS_FILE.read_text())
    except Exception:
        return None


def _json_load(path: str) -> dict | None:
    """Read a JSON file written by the orchestrator. Returns None on failure."""
    try:
        return _json.loads(Path(path).read_text())
    except Exception:
        logging.warning(f"⚠️ Failed to read JSON from {path}")
        return None


def _parse_structured_output(stdout: str | None) -> dict | None:
    """Extract structured_output from --output-format json result."""
    if not stdout:
        logging.error("❌ Orchestrator returned empty stdout — no structured output to parse.")
        return None
    try:
        data = _json.loads(stdout)
        # Primary path: CLI structured output (present when model's final response is valid JSON)
        structured = data.get("structured_output")
        if structured and isinstance(structured, dict):
            return structured
        # Fallback: model embedded JSON in the result text field
        result_str = data.get("result")
        if result_str:
            try:
                parsed = _json.loads(result_str)
                if isinstance(parsed, dict):
                    logging.warning("⚠️ structured_output absent; falling back to result field JSON.")
                    return parsed
            except Exception:
                pass
        logging.error(
            f"❌ No structured_output in orchestrator result. "
            f"Raw stdout prefix: {stdout[:300]!r}"
        )
        return None
    except Exception:
        logging.error(
            f"❌ Failed to parse orchestrator JSON output. "
            f"Raw stdout prefix: {stdout[:300]!r}"
        )
        return None


async def execution_phase(cfg: RuntimeConfig):
    logging.info("\n" + "="*50)
    logging.info("🚀 PLAN EXECUTION")
    logging.info("="*50)

    # Instantiate safeguards (shared across all phases in this run)
    rate_limiter = RateLimiter(hourly_call_limit=cfg.hourly_call_limit)
    circuit_breaker = CircuitBreaker()
    exit_gate = ExitGate()

    ex_state = load_execution_state(EXECUTION_STATE_FILE)
    completed_phases: list[str] = ex_state.get("completed_phases", []) if ex_state else []
    if ex_state:  # resuming — don't carry over stale no-progress counter
        circuit_breaker.close()
    if ex_state:
        logging.info(
            f"↩️  Resuming execution. Completed phases: {completed_phases}. "
            f"Last active phase: {ex_state.get('current_phase')}."
        )
        # Restore exit gate state so crash-resume doesn't reset the consecutive-signal counter
        saved_status = _load_status_json()
        if saved_status and saved_status.get("phase") == ex_state.get("current_phase"):
            exit_gate.restore_state(ExitGateState(
                consecutive_completion_signals=saved_status.get("exit_gate_consecutive_signals", 0),
                kpis_met_confirmed=saved_status.get("exit_gate_kpis_met", False),
                heuristic_score=saved_status.get("exit_gate_heuristic_score", 0),
                proceed_signal=saved_status.get("exit_gate_proceed_signal", False),
            ))
            logging.info(
                f"↩️  Restored ExitGate state: consecutive_signals="
                f"{saved_status.get('exit_gate_consecutive_signals', 0)}"
            )

    # Find all phase files generated in planning, ordered by prefix (phase_1, phase_2, etc.)
    phase_files = sorted(glob.glob(f"{PATH_PLANS}/phase_*_plan.md"))
    if not phase_files:
        logging.error("❌ No phase plan files found!")
        return

    # Loop through phases sequentially. Each phase has its own loop for iterative execution until KPIs are met.
    for phase_file in phase_files:
        phase_name = os.path.basename(phase_file).replace('_plan.md', '')
        memory_file = f"{PATH_LIVE_ARTIFACTS}/{phase_name}_memory.md"

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
        prior_review_data: dict | None = None

        async def _status_loop():
            """Background task: continuously writes orchestration state to status.json every 2 seconds.

            This closure captures `phase_name` and `loop_num` by reference, reading their live values
            as the phase executes. Runs independently from the main loop, allowing the dashboard
            to show active workers and status updates even during blocking orchestrator calls.
            """
            while True:
                # Capture current gate state for this status snapshot
                gate_state = exit_gate.get_state()

                # Write orchestration state to status.json (read by live_monitoring.py dashboard)
                write_status(
                    phase=phase_name,
                    loop_count=loop_num,  # reads live value as loop_num increments
                    program_calls_this_hour=rate_limiter.program_calls_this_hour,
                    circuit_breaker_state=circuit_breaker.get_state(),
                    exit_gate_heuristic=gate_state.heuristic_score,
                    exit_gate_kpis_met=gate_state.kpis_met_confirmed,
                    active_workers=get_active_workers(),  # captures worker IDs from live registry
                    cooldown_until=rate_limiter.cooldown_until,
                    hourly_call_limit=cfg.hourly_call_limit,
                    exit_gate_consecutive_signals=gate_state.consecutive_completion_signals,
                    exit_gate_proceed_signal=gate_state.proceed_signal,
                )

                # Update every 2 seconds to keep dashboard fresh
                await asyncio.sleep(2)

        status_task = asyncio.create_task(_status_loop())
        while loop_num <= cfg.n_max_loops:
            logging.info(f"\n🔄 [LOOP {loop_num}/{cfg.n_max_loops}] Planning execution...")

            # Capture baseline commit BEFORE any work so diff is loop-scoped
            baseline_commit = _get_baseline_commit()

            # Single-agent vs multi-agent path
            if cfg.n_sub_agents == 1:
                # Steps 1+2 combined: inline execution (no delegation/workers)
                if loop_num == 1:
                    exec_prompt = load_prompt(
                        _EXEC_PROMPTS, "single_agent_loop1",
                        phase_file=phase_file,
                        memory_file=memory_file,
                    )
                else:
                    proposed_fixes = (
                        prior_review_data.get('proposed_fixes_or_new_kpis', 'N/A')
                        if prior_review_data else 'N/A'
                    )
                    prior_kpi_status = (
                        'ALL MET'
                        if prior_review_data and prior_review_data.get('kpis_met')
                        else 'NOT YET MET'
                    )
                    exec_prompt = load_prompt(
                        _EXEC_PROMPTS, "single_agent_loop_n",
                        phase_file=phase_file,
                        memory_file=memory_file,
                        loop_num=loop_num,
                        proposed_fixes=proposed_fixes,
                        prior_kpi_status=prior_kpi_status,
                    )
                logging.info(f"📋 [{phase_name} loop {loop_num}] Single-agent combined step (task + execution)...")
                await run_orchestrator_async(exec_prompt, rate_limiter=rate_limiter, max_turns=cfg.max_turns)
                all_worker_outputs: list[str] = []
            else:
                # 1. Orchestrator plans tasks for workers
                if loop_num == 1:
                    task_prompt = load_prompt(
                        _EXEC_PROMPTS, "task_loop1",
                        phase_file=phase_file,
                        memory_file=memory_file,
                        n_sub_agents=cfg.n_sub_agents,
                    )
                else:
                    proposed_fixes = (
                        prior_review_data.get('proposed_fixes_or_new_kpis', 'N/A')
                        if prior_review_data else 'N/A'
                    )
                    prior_kpi_status = (
                        'ALL MET'
                        if prior_review_data and prior_review_data.get('kpis_met')
                        else 'NOT YET MET'
                    )
                    task_prompt = load_prompt(
                        _EXEC_PROMPTS, "task_loop_n",
                        phase_file=phase_file,
                        memory_file=memory_file,
                        loop_num=loop_num,
                        proposed_fixes=proposed_fixes,
                        prior_kpi_status=prior_kpi_status,
                        n_sub_agents=cfg.n_sub_agents,
                    )
                logging.info(f"📋 [{phase_name} loop {loop_num}] Step 1/4: Planning tasks for workers...")
                task_file = f"{PATH_LIVE_ARTIFACTS}/{phase_name}_tasks_{loop_num}.json"
                task_prompt += (
                    f"\n\nIMPORTANT: Write your output to the file '{task_file}' using your file writing tools. "
                    "Output ONLY a valid JSON object — no conversational text before or after the file."
                )
                await run_orchestrator_async(
                    task_prompt, rate_limiter=rate_limiter,
                    max_turns=cfg.max_turns,
                )
                task_data = _json_load(task_file)
                raw_bundles = (task_data.get("agent_bundles", []) if task_data else [])
                if len(raw_bundles) > cfg.n_sub_agents:
                    logging.warning(
                        f"⚠️ Orchestrator returned {len(raw_bundles)} bundles but n_sub_agents={cfg.n_sub_agents}. "
                        f"Truncating to first {cfg.n_sub_agents}. Excess work will be re-planned next loop."
                    )
                bundles = raw_bundles[:cfg.n_sub_agents]
                move_to_archive(task_file, PATH_ARCHIVED_ARTIFACTS)

                # 2. Execute Tasks via Workers (Bounded by cfg.n_sub_agents)
                all_worker_outputs: list[str] = []
                if bundles:
                    logging.info(f"🛠️ Orchestrator delegated {len(bundles)} execution bundles to workers.")
                    sem = asyncio.Semaphore(cfg.n_sub_agents)
                    worker_coroutines = [
                        run_worker_agent(
                            sem, i + 1, bundle,
                            loop_num=loop_num,
                            rate_limiter=rate_limiter,
                            max_turns=cfg.max_turns,
                        )
                        for i, bundle in enumerate(bundles)
                    ]
                    all_worker_outputs = list(await asyncio.gather(*worker_coroutines))
                else:
                    logging.info("No bundles delegated. Orchestrator believes phase might be complete.")

            # 3. Orchestrator Reviews Work against KPIs
            review_file = f"{PATH_LIVE_ARTIFACTS}/{phase_name}_review_{loop_num}.json"
            if loop_num == 1:
                review_prompt = load_prompt(
                    _EXEC_PROMPTS, "review_loop1",
                    phase_file=phase_file,
                )
            else:
                proposed_fixes = (
                    prior_review_data.get('proposed_fixes_or_new_kpis', 'N/A')
                    if prior_review_data else 'N/A'
                )
                review_prompt = load_prompt(
                    _EXEC_PROMPTS, "review_loop_n",
                    phase_file=phase_file,
                    loop_num=loop_num,
                    proposed_fixes=proposed_fixes,
                )
            logging.info(f"🔍 [{phase_name} loop {loop_num}] Step 3/4: Reviewing work against KPIs...")
            review_result = await run_orchestrator_async(
                review_prompt, rate_limiter=rate_limiter,
                max_turns=str(int(cfg.max_turns) * 3),
                output_format="json",
                json_schema=_REVIEW_SCHEMA,
            )
            review_data = _parse_structured_output(review_result)

            # Last-resort fallback: utility model extracts JSON from raw orchestrator output
            if review_data is None and review_result:
                logging.warning("⚠️ Structured output parsing failed — attempting utility-model JSON extraction...")
                extraction_prompt = load_prompt(
                    _EXEC_PROMPTS, "extract_review_json",
                    raw_output=review_result,
                    review_schema=_REVIEW_SCHEMA,
                )
                fallback_result = await run_orchestrator_async(
                    extraction_prompt,
                    rate_limiter=rate_limiter,
                    model=MODEL_UTILITY,
                    max_turns="3",
                    output_format="json",
                    json_schema=_REVIEW_SCHEMA,
                )
                review_data = _parse_structured_output(fallback_result)
                if review_data:
                    logging.info("✅ Utility-model extraction succeeded.")
                else:
                    logging.error("❌ Utility-model extraction also failed — review_data will be None.")

            # Python writes the JSON file for archiving (Claude no longer needs to)
            if review_data:
                Path(review_file).write_text(_json.dumps(review_data, indent=2))

            # Archive review file now that we have the parsed data
            move_to_archive(review_file, PATH_ARCHIVED_ARTIFACTS)

            # 4. Update Memory
            update_memory_prompt = load_prompt(
                _EXEC_PROMPTS, "update_memory",
                memory_file=memory_file,
                loop_num=loop_num,
                kpis_met=review_data.get('kpis_met', False) if review_data else False,
                any_new_kpi_satisfied=review_data.get('any_new_kpi_satisfied', False) if review_data else False,
                summary=review_data.get('summary', 'N/A') if review_data else 'N/A',
                proposed_fixes=review_data.get('proposed_fixes_or_new_kpis', 'N/A') if review_data else 'N/A',
            )
            logging.info(f"💾 [{phase_name} loop {loop_num}] Step 4/4: Updating phase memory...")
            await run_orchestrator_async(
                update_memory_prompt, rate_limiter=rate_limiter,
                model=MODEL_UTILITY, max_turns=cfg.max_turns,
            )

            # 5. Update Exit Gate
            # Also scan the review summary for completion keywords (e.g., "KPIs Met", "tests pass", "finished")
            # Wrap in a Summary heading so ExitGate section-scanning can find keywords
            outputs_for_gate = list(all_worker_outputs)
            if review_data and review_data.get("summary"):
                outputs_for_gate.append(f"## Summary\n{review_data['summary']}")
            exit_gate.record_worker_outputs(outputs_for_gate)
            exit_gate.record_kpi_review(
                kpis_met=review_data.get("kpis_met", False) if review_data else False
            )

            # Record explicit proceed signal when review outputs "NONE. Proceed to next phase."
            if review_data and review_data.get("proposed_fixes_or_new_kpis") == "NONE. Proceed to next phase.":
                exit_gate.record_proceed_signal()

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
                kpis_met_confirmed=review_data.get("kpis_met", False) if review_data else False,
            )
            logging.info(
                f"📊 [{phase_name} loop {loop_num}] Metrics — "
                f"files changed: {files_changed}, artifacts: {artifacts_produced}, "
                f"KPI advance: {kpi_advancement}"
                + (f", error sig: {error_sig}" if error_sig else "")
            )
            prior_review_data = review_data

            # 7. Write status.json (enables monitoring and crash recovery)
            gate_state = exit_gate.get_state()
            write_status(
                phase=phase_name,
                loop_count=loop_num,
                program_calls_this_hour=rate_limiter.program_calls_this_hour,
                circuit_breaker_state=circuit_breaker.get_state(),
                exit_gate_heuristic=gate_state.heuristic_score,
                exit_gate_kpis_met=gate_state.kpis_met_confirmed,
                active_workers=get_active_workers(),
                cooldown_until=rate_limiter.cooldown_until,
                hourly_call_limit=cfg.hourly_call_limit,
                exit_gate_consecutive_signals=gate_state.consecutive_completion_signals,
                exit_gate_proceed_signal=gate_state.proceed_signal,
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
                overridden = await _wait_for_cooldown_async(circuit_breaker.remaining_cooldown_seconds())
                if overridden:
                    circuit_breaker.check_cooldown()
                    if circuit_breaker.is_open():
                        # Cooldown hasn't elapsed yet — force-close anyway (user override)
                        circuit_breaker.close()
                continue  # loop_num NOT incremented — counter paused during cooldown

            # 9. Exit Gate check — replaces the old bare `if kpis_met: break`
            if exit_gate.should_exit():
                logging.info("✅ Exit gate opened — phase complete.")
                break

            # 10. HITL / Unattended Feedback
            if review_data and not review_data.get("kpis_met"):
                if cfg.unattended_mode:
                    # Agent generates feedback as a human would
                    proposed_fixes = review_data.get('proposed_fixes_or_new_kpis', 'N/A')
                    feedback_prompt = load_prompt(
                        _EXEC_PROMPTS, "unattended_feedback",
                        phase_file=phase_file,
                        memory_file=memory_file,
                        proposed_fixes=proposed_fixes,
                    )
                    logging.info("🤖 [AUTONOMOUS] KPIs not met — orchestrator generating feedback...")
                    feedback_file = f"{PATH_LIVE_ARTIFACTS}/{phase_name}_feedback_{loop_num}.json"
                    feedback_prompt += (
                        f"\n\nIMPORTANT: Write your feedback to the file '{feedback_file}' using your file writing tools. "
                        "Output ONLY a valid JSON object with a 'feedback' field — no conversational text."
                    )
                    await run_orchestrator_async(
                        feedback_prompt, rate_limiter=rate_limiter, max_turns=cfg.max_turns,
                    )
                    feedback_result = _json_load(feedback_file)
                    feedback = (feedback_result.get('feedback') if isinstance(feedback_result, dict) else None) if feedback_result else None
                    move_to_archive(feedback_file, PATH_ARCHIVED_ARTIFACTS)
                    if feedback:
                        with open(memory_file, "a") as f:
                            f.write(f"\nOrchestrator Feedback (autonomous): {feedback}\n")
                else:
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
                        try:
                            user_input = input(
                                "👨‍💻 HUMAN INPUT: Type 'continue' to let AI fix this "
                                "in the next loop, or provide specific guidance/new KPIs: "
                            )
                        except KeyboardInterrupt:
                            print()
                            logging.info("⏸️  Break-off requested during execution. State saved. Re-run to resume.")
                            return
                    if user_input.lower() not in ['continue', 'c', 'yes', 'y']:
                        with open(memory_file, "a") as f:
                            f.write(f"\nHuman Feedback for next loop: {user_input}\n")

            loop_num += 1

        else:
            logging.info(
                f"\n⚠️ Reached maximum loops ({cfg.n_max_loops}) for {phase_name}. "
                "Forcing progression."
            )

        # Phase Completion Report
        report_prompt = load_prompt(
            _EXEC_PROMPTS, "report",
            phase_name=phase_name,
            memory_file=memory_file,
            path_artifacts=PATH_LIVE_ARTIFACTS,
        )
        logging.info(f"📝 [{phase_name}] Generating phase completion report...")
        await run_orchestrator_async(report_prompt, rate_limiter=rate_limiter, max_turns=cfg.max_turns)
        logging.info(f"📝 Generated {phase_name}_report.md")

        # Git commit with orchestrator-generated message
        commit_msg_prompt = load_prompt(
            _EXEC_PROMPTS, "commit_message",
            phase_name=phase_name,
            memory_file=memory_file,
            path_artifacts=PATH_LIVE_ARTIFACTS,
        )
        logging.info(f"📝 [{phase_name}] Generating git commit message...")
        commit_result = await run_orchestrator_async(
            commit_msg_prompt, rate_limiter=rate_limiter,
            model=MODEL_UTILITY, max_turns=cfg.max_turns,
        )
        commit_message = commit_result.strip() if isinstance(commit_result, str) else f"chore: complete {phase_name}"
        logging.info(f"📝 [{phase_name}] Committing with message: {commit_message}")

        # Perform git commit
        import subprocess
        try:
            subprocess.run(["git", "add", "-A"], capture_output=True, timeout=10)
            result = subprocess.run(
                ["git", "commit", "-m", commit_message],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                logging.info(f"✅ [{phase_name}] Committed: {result.stdout.strip()}")
            else:
                logging.warning(f"⚠️ [{phase_name}] Commit failed: {result.stderr.strip()}")
        except Exception as e:
            logging.warning(f"⚠️ [{phase_name}] Git commit error: {e}")

        # Clean transient artifacts (phase reports are preserved)
        clean_transient_artifacts()

        # Close the circuit breaker so the next phase starts clean (Bug 2)
        circuit_breaker.close()

        move_to_archive(memory_file, PATH_ARCHIVED_MEMORY)
        completed_phases.append(phase_name)
        save_execution_state(EXECUTION_STATE_FILE, completed_phases, phase_name, loop_num)

        status_task.cancel()
        try:
            await status_task
        except asyncio.CancelledError:
            pass

    logging.info("\n🎉 ALL PHASES COMPLETED SUCCESSFULLY! 🎉")
