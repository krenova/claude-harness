import asyncio
import logging
import os
from pathlib import Path

from config import (
    PATH_PLANS,
    PATH_ARCHIVED_MEMORY,
    PATH_ARCHIVED_ARTIFACTS,
    MODEL_UTILITY,
    PLANNING_MEMORY_FILE,
    PLANNING_STATE_FILE,
    RISK_ASSESSMENT_FILE,
    HUMAN_FEEDBACK_FILE,
    RuntimeConfig,
)
from src.agents.orchestrator import run_orchestrator_async
from src.agents.worker import run_worker_agent
from src.helpers import load_planning_state, save_planning_state, move_to_archive
from src.safeguards import RateLimiter
from src.safeguards.status_writer import write_status, get_active_workers
from src.prompts.loader import load_prompt

_PLAN_PROMPTS = Path(__file__).parent.parent / "prompts" / "workflows" / "plan_refinement.yaml"


async def plan_refinement_phase(cfg: RuntimeConfig) -> bool:
    logging.info("\n" + "="*50)
    logging.info("🎯 PLAN REVIEW & REFINEMENT")
    logging.info("="*50)

    rate_limiter = RateLimiter(hourly_call_limit=cfg.hourly_call_limit)

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
        write_status(
            phase="planning",
            loop_count=iteration,
            api_calls_this_hour=rate_limiter.api_calls_this_hour,
            circuit_breaker_state="N/A",
            exit_gate_heuristic=0,
            exit_gate_kpis_met=False,
            active_workers=get_active_workers(),
            rate_limit_cooldown_until=rate_limiter.rate_limit_cooldown_until,
        )

    while True:
        if not resuming:
            async def _status_loop():
                """Background task: writes planning state to status.json every 2s.

                Captures `iteration` and `rate_limiter` by reference so the dashboard
                shows live API call counts and active research workers during
                orchestrator calls and asyncio.gather().
                """
                while True:
                    write_status(
                        phase="planning",
                        loop_count=iteration,
                        api_calls_this_hour=rate_limiter.api_calls_this_hour,
                        circuit_breaker_state="N/A",
                        exit_gate_heuristic=0,
                        exit_gate_kpis_met=False,
                        active_workers=get_active_workers(),
                        rate_limit_cooldown_until=rate_limiter.rate_limit_cooldown_until,
                    )
                    await asyncio.sleep(2)

            status_task = asyncio.create_task(_status_loop())

            # ── Single-agent vs multi-agent path ───────────────────────────────
            if cfg.n_sub_agents == 1:
                memory_context = ""
                if os.path.exists(PLANNING_MEMORY_FILE):
                    with open(PLANNING_MEMORY_FILE, "r") as f:
                        memory_context = f.read()

                logging.info(f"📋 [Planning iter {iteration}] Single-agent combined step (delegation + research + plan)...")
                single_agent_prompt = load_prompt(
                    _PLAN_PROMPTS,
                    "single_agent_iter1" if iteration == 1 else "single_agent_iter_n",
                    path_plans=PATH_PLANS,
                    memory_context=memory_context,
                )
                await run_orchestrator_async(single_agent_prompt, rate_limiter=rate_limiter, max_turns=cfg.max_turns)
            else:
                # ── Step 1: Research delegation ──────────────────────────────────
                memory_context = ""
                if os.path.exists(PLANNING_MEMORY_FILE):
                    with open(PLANNING_MEMORY_FILE, "r") as f:
                        memory_context = f.read()

                if iteration == 1:
                    delegation_prompt = load_prompt(
                        _PLAN_PROMPTS, "delegation_iter1",
                        path_plans=PATH_PLANS,
                        memory_context=memory_context,
                        n_sub_agents=cfg.n_sub_agents,
                    )
                else:
                    delegation_prompt = load_prompt(
                        _PLAN_PROMPTS, "delegation_iter_n",
                        path_plans=PATH_PLANS,
                        memory_context=memory_context,
                        n_sub_agents=cfg.n_sub_agents,
                    )
                logging.info(f"📋 [Planning iter {iteration}] Step 1/4: Delegating research tasks...")
                delegation_data = await run_orchestrator_async(
                    delegation_prompt, require_json=True, rate_limiter=rate_limiter,
                    max_turns=cfg.max_turns,
                )

                # ── Step 2: Research workers ──────────────────────────────────────
                if delegation_data and delegation_data.get("agent_bundles"):
                    raw_bundles = delegation_data["agent_bundles"]
                    if len(raw_bundles) > cfg.n_sub_agents:
                        logging.warning(
                            f"⚠️ Orchestrator returned {len(raw_bundles)} bundles but n_sub_agents={cfg.n_sub_agents}. "
                            f"Truncating to first {cfg.n_sub_agents}. Excess work will be re-planned next loop."
                        )
                    bundles = raw_bundles[:cfg.n_sub_agents]
                    logging.info(f"\n🔍 Orchestrator delegated {len(bundles)} research bundles to workers.")
                    sem = asyncio.Semaphore(cfg.n_sub_agents)
                    worker_coroutines = [
                        run_worker_agent(sem, i+1, bundle, rate_limiter=rate_limiter, max_turns=cfg.max_turns)
                        for i, bundle in enumerate(bundles)
                    ]
                    research_results = await asyncio.gather(*worker_coroutines)
                    research_context = "\n".join(research_results)
                else:
                    research_context = "No additional research was required."

                # ── Step 3: Plan generation ───────────────────────────────────────
                if iteration == 1:
                    split_plan_prompt = load_prompt(
                        _PLAN_PROMPTS, "split_iter1",
                        research_context=research_context,
                        path_plans=PATH_PLANS,
                    )
                else:
                    split_plan_prompt = load_prompt(
                        _PLAN_PROMPTS, "split_iter_n",
                        research_context=research_context,
                        path_plans=PATH_PLANS,
                    )
                logging.info(f"📝 [Planning iter {iteration}] Step 3/4: Generating plan split from research...")
                await run_orchestrator_async(split_plan_prompt, rate_limiter=rate_limiter, max_turns=cfg.max_turns)

            # ── Step 3.5: Risk assessment ────────────────────────────────────
            clarification_prompt = load_prompt(
                _PLAN_PROMPTS, "clarification",
                path_plans=PATH_PLANS,
                risk_assessment_file=RISK_ASSESSMENT_FILE,
            )
            logging.info(f"⚠️  [Planning iter {iteration}] Step 3.5/4: Running risk assessment...")
            clarification_report = await run_orchestrator_async(
                clarification_prompt, rate_limiter=rate_limiter, max_turns=cfg.max_turns,
            )

            # Persist risk assessment to file (in case the orchestrator didn't write it)
            if clarification_report:
                with open(RISK_ASSESSMENT_FILE, "w") as f:
                    f.write(clarification_report)

            # ── Update planning memory ────────────────────────────────────────
            update_memory_prompt = load_prompt(
                _PLAN_PROMPTS, "update_memory",
                iteration=iteration,
                planning_memory_file=PLANNING_MEMORY_FILE,
            )
            logging.info(f"💾 [Planning iter {iteration}] Step 4/4: Updating planning memory...")
            await run_orchestrator_async(
                update_memory_prompt, rate_limiter=rate_limiter,
                model=MODEL_UTILITY, max_turns=cfg.max_turns,
            )

            # Save state as awaiting_review before blocking on HITL
            save_planning_state(PLANNING_STATE_FILE, "awaiting_review", iteration)

            # Cancel background status loop before HITL — nothing to monitor during user input
            status_task.cancel()
            try:
                await status_task
            except asyncio.CancelledError:
                pass

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
            if cfg.unattended_mode:
                # Agent generates approval/guidance as a human would
                feedback_prompt = load_prompt(
                    _PLAN_PROMPTS, "unattended_feedback",
                    path_plans=PATH_PLANS,
                    risk_assessment_file=RISK_ASSESSMENT_FILE,
                )
                logging.info("🤖 [AUTONOMOUS] Orchestrator generating planning feedback...")
                feedback_result = await run_orchestrator_async(
                    feedback_prompt, rate_limiter=rate_limiter, max_turns=cfg.max_turns
                )
                user_input = feedback_result.get('feedback', 'approve')
            else:
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
            # Ensure the feedback file exists for the user to write into (avoids confusion about where to write feedback)
            if not os.path.exists(HUMAN_FEEDBACK_FILE):
                open(HUMAN_FEEDBACK_FILE, "w").close()  # create empty file if it doesn't exist
            return False

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
            f.write(f"- Human feedback (iteration {iteration}): {user_input}\n")

        fix_prompt = load_prompt(
            _PLAN_PROMPTS, "fix",
            user_input=user_input,
            path_plans=PATH_PLANS,
        )
        logging.info(f"🔧 [Planning iter {iteration}] Applying human feedback — replanning...")
        await run_orchestrator_async(fix_prompt, rate_limiter=rate_limiter, max_turns=cfg.max_turns)

        iteration += 1
        resuming = False  # next loop runs full research + plan cycle
        save_planning_state(PLANNING_STATE_FILE, "in_progress", iteration)

    return True
