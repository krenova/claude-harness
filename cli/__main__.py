#!/usr/bin/env python3
"""
Claude Autonomous Harness CLI

Usage:
    harness init <project_dir>     Initialize a project with harness directory structure
    harness run [options]         Run the harness (planning/execution/both)
    harness status [options]       Show current execution status

Options:
    -C, --project-dir DIR          Project directory (default: current directory)
    --mode MODE                    planning|execution|full (default: full)
    --sub-agents N                 Max concurrent workers (default: 1)
    --max-loops N                  Max loops per phase (default: 3)
    --max-turns N                  Max tool turns per session (default: 15)
    --hourly-limit N               Max program calls per hour before cooldown (default: 20)
    --autonomous                   Run in autonomous mode (default: attended)

Examples:
    harness init ./my-project      # Initialize project structure
    harness run -C ./my-project     # Run harness on project
    harness run -C . --autonomous   # Run in current directory, autonomous mode
"""
import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

import click

# Add parent to path for imports when running as script
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    PATH_PLANS,
    PATH_ARTIFACTS,
    PATH_LIVE_ARTIFACTS,
    PATH_ARCHIVED_MEMORY,
    PATH_ARCHIVED_ARTIFACTS,
    PATH_LOGS,
    RuntimeConfig,
)
from src.workflows import execution_phase, plan_refinement_phase


def setup_logging(project_dir: str):
    """Configure logging to write to project's .logs directory."""
    plans_dir = os.path.join(project_dir, "plans")
    logs_dir = os.path.join(project_dir, ".logs")

    os.makedirs(logs_dir, exist_ok=True)

    # Reconfigure root logger
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(module)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        filename=f"{logs_dir}/orchestrator.log",
        filemode="a",
    )
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(module)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger.addHandler(console_handler)
    root_logger.setLevel(logging.INFO)


@click.group()
@click.version_option(version="1.0.0")
def cli():
    """Claude Autonomous Harness - Run AI-powered development workflows on any project."""
    pass


@cli.command()
@click.argument("project_dir", type=click.Path())
@click.option(
    "--force/--no-force",
    default=False,
    help="Overwrite existing harness directories if they exist",
)
def init(project_dir: str, force: bool):
    """Initialize a project with harness directory structure.

    Creates plans/, .artifacts/, .logs/, .archived_memory/, .archived_artifacts/
    directories and an example initial_plan.md if none exists.
    """
    project_path = Path(project_dir).resolve()
    if not project_path.exists():
        project_path.mkdir(parents=True)
        click.echo(f"Created project directory: {project_path}")

    # Create directory structure
    dirs_to_create = [
        ("plans", PATH_PLANS),
        (".artifacts/live_artifacts", PATH_LIVE_ARTIFACTS),
        (".artifacts/archived_artifacts", PATH_ARCHIVED_ARTIFACTS),
        (".artifacts/archived_memory", PATH_ARCHIVED_MEMORY),
        (".logs", PATH_LOGS),
    ]

    for rel_name, _ in dirs_to_create:
        full_path = project_path / rel_name
        if full_path.exists():
            if force:
                click.echo(f"Overwriting existing: {full_path}")
            else:
                click.echo(f"Skipping existing: {full_path}")
                continue
        full_path.mkdir(parents=True, exist_ok=True)
        click.echo(f"Created: {full_path}")

    # Create example initial_plan.md if none exists
    initial_plan = project_path / "plans" / "initial_plan.md"
    if not initial_plan.exists():
        example_plan = """# Initial Plan

## Objective
Describe what you want to accomplish with this project.

## Scope
- Feature 1
- Feature 2

## KPIs
- [ ] KPI 1: Description
- [ ] KPI 2: Description

## Notes
Add any additional context or constraints here.
"""
        initial_plan.write_text(example_plan)
        click.echo(f"Created example plan: {initial_plan}")
        click.echo("\nEdit this file to define your project's initial plan, then run:")
        click.echo(f"  harness run -C {project_dir}")
    else:
        click.echo(f"Found existing: {initial_plan}")
        click.echo("\nRun the harness with:")
        click.echo(f"  harness run -C {project_dir}")

    click.echo("\nHarness initialized successfully!")


@cli.command()
@click.option(
    "-C",
    "--project-dir",
    type=click.Path(exists=True),
    default=".",
    help="Project directory (default: current directory)",
)
@click.option(
    "--mode",
    type=click.Choice(["planning", "execution", "full"]),
    default="full",
    help="Which phases to run (default: full)",
)
@click.option(
    "--sub-agents",
    type=int,
    default=1,
    help="Max concurrent worker agents (default: 1)",
)
@click.option(
    "--max-loops",
    type=int,
    default=3,
    help="Max execution loops per phase (default: 3)",
)
@click.option(
    "--max-turns",
    type=int,
    default=15,
    help="Max autonomous tool turns per session (default: 15)",
)
@click.option(
    "--hourly-limit",
    type=int,
    default=20,
    help="Max program calls per hour before rate-limit cooldown (default: 20)",
)
@click.option(
    "--autonomous/--attended",
    default=False,
    help="Run in autonomous (unattended) mode (default: attended)",
)
def run(
    project_dir: str,
    mode: str,
    sub_agents: int,
    max_loops: int,
    max_turns: int,
    hourly_limit: int,
    autonomous: bool,
):
    """Run the Claude Autonomous Harness on a project."""
    project_path = Path(project_dir).resolve()

    # Validate that plans directory exists
    plans_dir = project_path / "plans"
    if not plans_dir.exists():
        click.echo(
            f"Error: {plans_dir} does not exist. Run 'harness init {project_dir}' first.",
            err=True,
        )
        sys.exit(1)

    # Validate that initial_plan.md exists
    initial_plan = plans_dir / "initial_plan.md"
    if not initial_plan.exists():
        click.echo(
            f"Error: {initial_plan} does not exist. Create an initial plan first.",
            err=True,
        )
        sys.exit(1)

    # Change to project directory so all paths are relative to it
    original_cwd = os.getcwd()
    os.chdir(project_path)

    try:
        # Setup logging for this project
        setup_logging(str(project_path))

        # Patch config paths for this project
        _patch_config_paths(str(project_path))

        logging.info(f"🚀 Starting harness in {project_path}")
        logging.info(f"📋 Mode: {mode}, Autonomous: {autonomous}")

        cfg = RuntimeConfig(
            n_sub_agents=sub_agents,
            n_max_loops=max_loops,
            max_turns=str(max_turns),
            unattended_mode=autonomous,
            hourly_call_limit=hourly_limit,
        )

        async def main():
            if mode == "planning":
                logging.info("📋 Planning Review.")
                await plan_refinement_phase(cfg)
            elif mode == "execution":
                logging.info("⏭️ Plan Execution.")
                await execution_phase(cfg)
            else:  # "full"
                logging.info("🚀 Full Run: Planning + Execution.")
                if await plan_refinement_phase(cfg):
                    await execution_phase(cfg)

        asyncio.run(main())
        click.echo("\n✅ Harness completed successfully!")

    except KeyboardInterrupt:
        click.echo("\n⚠️ Harness interrupted by user. State has been saved.")
        sys.exit(130)
    except Exception as e:
        logging.exception("Harness failed with error:")
        click.echo(f"\n❌ Harness failed: {e}", err=True)
        sys.exit(1)
    finally:
        os.chdir(original_cwd)


@cli.command()
@click.option(
    "-C",
    "--project-dir",
    type=click.Path(exists=True),
    default=".",
    help="Project directory (default: current directory)",
)
def status(project_dir: str):
    """Show current execution status for a project."""
    project_path = Path(project_dir).resolve()

    artifacts_dir = project_path / ".artifacts" / "live_artifacts"
    plans_dir = project_path / "plans"

    # Check status.json
    status_file = artifacts_dir / "status.json"
    if status_file.exists():
        import json

        with open(status_file) as f:
            data = json.load(f)
        click.echo(f"Phase: {data.get('phase', 'N/A')}")
        click.echo(f"Loop: {data.get('loop_count', 'N/A')}")
        click.echo(f"Heuristic Score: {data.get('exit_gate_heuristic', 'N/A')}")
        click.echo(f"KPIs Met: {data.get('exit_gate_kpis_met', 'N/A')}")
        click.echo(f"Active Workers: {data.get('active_workers', 'N/A')}")
    else:
        click.echo("No status.json found. Harness may not have run yet.")

    # Check for completed phases
    state_file = plans_dir / "execution_state.json"
    if state_file.exists():
        import json

        with open(state_file) as f:
            data = json.load(f)
        completed = data.get("completed_phases", [])
        if completed:
            click.echo(f"\nCompleted phases: {', '.join(completed)}")
        else:
            click.echo("\nNo phases completed yet.")


@cli.command()
@click.option(
    "-C",
    "--project-dir",
    type=click.Path(exists=True),
    default=".",
    help="Project directory (default: current directory)",
)
@click.confirmation_option(prompt="Are you sure you want to clear all harness state?")
def clean(project_dir: str, **kwargs):
    """Clear all harness state (artifacts, logs, archived files)."""
    project_path = Path(project_dir).resolve()

    dirs_to_clean = [
        project_path / ".artifacts",
        project_path / ".logs",
    ]

    for d in dirs_to_clean:
        if d.exists():
            shutil.rmtree(d)
            click.echo(f"Removed: {d}")
            d.mkdir(exist_ok=True)
            click.echo(f"Recreated empty: {d}")

    # Remove state files in plans/
    plans_dir = project_path / "plans"
    state_files = [
        "planning_state.json",
        "execution_state.json",
        "planning_memory.md",
        "execution_feedback.md",
        "human_feedback.md",
    ]
    for f in state_files:
        fp = plans_dir / f
        if fp.exists():
            fp.unlink()
            click.echo(f"Removed: {fp}")

    click.echo("\n✅ All harness state cleared!")


@cli.command()
@click.option(
    "-C",
    "--project-dir",
    type=click.Path(exists=True),
    default=".",
    help="Project directory (default: current directory)",
)
def monitor(project_dir: str):
    """Launch the live monitoring dashboard for a running harness."""
    project_path = Path(project_dir).resolve()
    original_cwd = os.getcwd()
    os.chdir(project_path)
    try:
        import live_monitoring
        asyncio.run(live_monitoring.main())
    except KeyboardInterrupt:
        click.echo("\n👋 Monitor stopped.")
    finally:
        os.chdir(original_cwd)


def _patch_config_paths(project_dir: str):
    """Patch config module to use project-specific paths."""
    import config

    config.PATH_PLANS = os.path.join(project_dir, "plans")
    config.PATH_ARTIFACTS = os.path.join(project_dir, ".artifacts")
    config.PATH_LIVE_ARTIFACTS = os.path.join(project_dir, ".artifacts/live_artifacts")
    config.PATH_LOGS = os.path.join(project_dir, ".logs")
    config.PATH_ARCHIVED_ARTIFACTS = os.path.join(project_dir, ".artifacts/archived_artifacts")
    config.PATH_ARCHIVED_MEMORY = os.path.join(project_dir, ".artifacts/archived_memory")
    config.PATH_IMPLEMENTATIONS = os.path.join(project_dir, ".implementations")
    config.PLANNING_MEMORY_FILE = os.path.join(config.PATH_PLANS, "planning_memory.md")
    config.PLANNING_STATE_FILE = os.path.join(config.PATH_PLANS, "planning_state.json")
    config.RISK_ASSESSMENT_FILE = os.path.join(config.PATH_PLANS, "risk_assessment.md")
    config.HUMAN_FEEDBACK_FILE = os.path.join(config.PATH_PLANS, "human_feedback.md")
    config.EXECUTION_STATE_FILE = os.path.join(config.PATH_PLANS, "execution_state.json")
    config.EXECUTION_FEEDBACK_FILE = os.path.join(
        config.PATH_PLANS, "execution_feedback.md"
    )
    config.RATE_LIMITER_STATE_FILE = os.path.join(
        config.PATH_LIVE_ARTIFACTS, "rate_limiter_state.json"
    )


if __name__ == "__main__":
    cli()
