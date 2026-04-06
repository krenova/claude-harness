import asyncio
import logging
import os

from config import (
    PATH_PLANS,
    PATH_ARTIFACTS,
    PATH_ARCHIVED_MEMORY,
    PATH_ARCHIVED_ARTIFACTS,
    PATH_LOGS,
    N_SUB_AGENTS,
    N_MAX_LOOPS,
    MAX_TURNS,
    AUTONOMOUS_MODE,
    HOURLY_CALL_LIMIT,
    RuntimeConfig,
)
from src.workflows import execution_phase, plan_refinement_phase

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
        "--hourly-limit",
        type=int,
        default=HOURLY_CALL_LIMIT,
        metavar="N",
        help=f"Max orchestrator+worker API calls per hour before rate-limit cooldown (default: {HOURLY_CALL_LIMIT})",
    )
    parser.add_argument(
        "--autonomous",
        type=int,
        default=AUTONOMOUS_MODE,
        choices=[0, 1],
        help="Run in autonomous mode (1) or attended mode (0). Default: 0",
    )
    args = parser.parse_args()

    cfg = RuntimeConfig(
        n_sub_agents=args.sub_agents,
        n_max_loops=args.max_loops,
        max_turns=str(args.max_turns),
        unattended_mode=bool(args.autonomous),
        hourly_call_limit=args.hourly_limit,
    )

    async def main():
        if args.mode == "planning":
            logging.info("📋 Planning Review.")
            await plan_refinement_phase(cfg)
        elif args.mode == "execution":
            logging.info("⏭️ Plan Execution.")
            await execution_phase(cfg)
        else:  # "full"
            logging.info("🚀 Full Run: Planning + Execution.")
            if await plan_refinement_phase(cfg):
                await execution_phase(cfg)

    asyncio.run(main())
