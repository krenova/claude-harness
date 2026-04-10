#!/usr/bin/env python3
"""
Claude Autonomous Harness CLI

Usage:
    harness init [PATH]              Initialize a project (default: current directory)
    harness run [PATH] [options]      Run the harness (default: current directory)
    harness status [PATH]             Show current execution status
    harness clean [PATH]              Clear harness state
    harness archive [PATH]           Archive current run and reset workspace
    harness monitor [PATH]           Launch live monitoring dashboard

Options:
    --mode MODE                    planning|execution|full (default: full)
    --sub-agents N                 Max concurrent workers (default: 1)
    --max-loops N                  Max loops per phase (default: 3)
    --max-turns N                  Max tool turns per session (default: 15)
    --hourly-limit N               Max program calls per hour before cooldown (default: 20)
    --autonomous                   Run in autonomous mode (default: attended)

Examples:
    harness init                       # Initialize in current directory
    harness init ./my-project          # Initialize in specific directory
    harness run                        # Run on current directory
    harness run ./my-project          # Run on specific directory
    harness run --autonomous          # Run in autonomous mode
    harness archive                   # Archive current run and reset
"""
import asyncio
import glob
import json
import logging
import os
import shutil
import sys
import zipfile
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


# ============================================================
# Project directory context
# ============================================================

class ProjectContext:
    """Holds project directory path and provides path utilities."""
    def __init__(self, path: str = "."):
        self.path = Path(path).resolve()


def _resolve_project_dir(ctx: click.Context, param: click.Parameter, value: str | None) -> Path:
    """Resolve project directory, defaulting to current directory."""
    if value is None:
        value = "."
    return Path(value).resolve()


def _patch_config_paths(project_dir: Path):
    """Patch config module to use project-specific paths."""
    import config

    project_str = str(project_dir)
    config.PATH_PLANS = os.path.join(project_str, "plans")
    config.PATH_ARTIFACTS = os.path.join(project_str, ".artifacts")
    config.PATH_LIVE_ARTIFACTS = os.path.join(project_str, ".artifacts/live_artifacts")
    config.PATH_LOGS = os.path.join(project_str, ".logs")
    config.PATH_ARCHIVED_ARTIFACTS = os.path.join(project_str, ".artifacts/archived_artifacts")
    config.PATH_ARCHIVED_MEMORY = os.path.join(project_str, ".artifacts/archived_memory")
    config.PATH_IMPLEMENTATIONS = os.path.join(project_str, ".implementations")
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


def setup_logging(project_dir: Path):
    """Configure logging to write to project's .logs directory."""
    logs_dir = project_dir / ".logs"

    os.makedirs(logs_dir, exist_ok=True)

    # Reconfigure root logger
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(module)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        filename=str(logs_dir / "orchestrator.log"),
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


# ============================================================
# CLI Commands
# ============================================================

@click.group()
@click.version_option(version="1.0.0")
def cli():
    """Claude Autonomous Harness - Run AI-powered development workflows on any project."""
    pass


@cli.command()
@click.argument("path", default=".", required=False)
@click.option(
    "--force/--no-force",
    default=False,
    help="Overwrite existing harness directories if they exist",
)
def init(path: str, force: bool):
    """Initialize a project with harness directory structure.

    Creates plans/, .artifacts/, .logs/ directories and an example
    initial_plan.md if none exists.

    PATH is optional (default: current directory).
    """
    project_path = Path(path).resolve()
    if not project_path.exists():
        project_path.mkdir(parents=True)
        click.echo(f"Created project directory: {project_path}")

    # Create directory structure
    dirs_to_create = [
        "plans",
        ".artifacts/live_artifacts",
        ".artifacts/archived_artifacts",
        ".artifacts/archived_memory",
        ".logs",
    ]

    for rel_name in dirs_to_create:
        full_path = project_path / rel_name
        if full_path.exists():
            if force:
                click.echo(f"Overwriting existing: {full_path}")
            else:
                click.echo(f"Skipping existing: {full_path}")
                continue
        full_path.mkdir(parents=True, exist_ok=True)
        click.echo(f"Created: {full_path}")

    # Create .claude/settings.json with permissions
    claude_settings_dir = project_path / ".claude"
    claude_settings_dir.mkdir(exist_ok=True)
    settings_file = claude_settings_dir / "settings.json"

    claude_settings = {
        "permissions": {
            "deny": [
                "Bash(brew install *)",
                "Bash(apt install *)",
                "Bash(yum install *)"
            ]
        },
        "sandbox": {
            "enabled": true,
            "allowUnsandboxedCommands": false,
            "failIfUnavailable": true,
            "filesystem": {
                "denyRead": ["~/"],
                "allowRead": ["."]
            }
        }
    }

    settings_file.write_text(json.dumps(claude_settings, indent=2))
    click.echo(f"Created: {settings_file}")

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

## Development Instructions

  **IMPORTANT:** This project runs in a sandboxed environment.

  **Allowed local execution:**
  - For python projects, always set up Python virtual environment:`python -m venv .venv` and `source .venv/bin/activate`
  - Node.js or other javascript or typescript projects: use `pnpm` directly

  **All other programs (databases, caches, services, etc.):**
  - Must be run via Docker or docker-compose
  - Do NOT attempt to install programs locally (brew, apt, yum, etc.)
  - If you need a service (PostgreSQL, Redis, MongoDB, etc.), create a
  docker-compose.yml and run it with `docker-compose up`

  **Examples:**
  - ✅ `docker-compose up -d` to start PostgreSQL
  - ✅ `docker-compose run --rm app pytest tests/` to run tests
  - ❌ `brew install postgresql` - BLOCKED
  - ❌ `apt-get install redis` - BLOCKED


## Notes
Add any additional context or constraints here.
"""
        initial_plan.write_text(example_plan)
        click.echo(f"Created example plan: {initial_plan}")
        click.echo("\nEdit this file to define your project's initial plan, then run:")
        click.echo(f"  harness run {path}")
    else:
        click.echo(f"Found existing: {initial_plan}")
        click.echo("\nRun the harness with:")
        click.echo(f"  harness run {path}")

    click.echo("\nHarness initialized successfully!")


@cli.command()
@click.argument("path", default=".", required=False, callback=_resolve_project_dir)
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
    path: Path,
    mode: str,
    sub_agents: int,
    max_loops: int,
    max_turns: int,
    hourly_limit: int,
    autonomous: bool,
):
    """Run the Claude Autonomous Harness on a project.

    PATH is optional (default: current directory).
    """
    # Validate that plans directory exists
    plans_dir = path / "plans"
    if not plans_dir.exists():
        click.echo(
            f"Error: {plans_dir} does not exist. Run 'harness init {path}' first.",
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
    os.chdir(path)

    try:
        # Setup logging for this project
        setup_logging(path)

        # Patch config paths for this project
        _patch_config_paths(path)

        logging.info(f"🚀 Starting harness in {path}")
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
@click.argument("path", default=".", required=False, callback=_resolve_project_dir)
def status(path: Path):
    """Show current execution status for a project.

    PATH is optional (default: current directory).
    """
    artifacts_dir = path / ".artifacts" / "live_artifacts"
    plans_dir = path / "plans"

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
@click.argument("path", default=".", required=False, callback=_resolve_project_dir)
@click.confirmation_option(prompt="Are you sure you want to clear all harness state?")
def clean(path: Path, **kwargs):
    """Clear all harness state (artifacts, logs).

    PATH is optional (default: current directory).
    """
    dirs_to_clean = [
        path / ".artifacts",
        path / ".logs",
    ]

    for d in dirs_to_clean:
        if d.exists():
            shutil.rmtree(d)
            click.echo(f"Removed: {d}")
            d.mkdir(exist_ok=True)
            click.echo(f"Recreated empty: {d}")

    # Remove state files in plans/
    plans_dir = path / "plans"
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
@click.argument("path", default=".", required=False, callback=_resolve_project_dir)
@click.confirmation_option(
    prompt="Are you sure you want to archive this implementation and reset the workspace?"
)
def archive(path: Path, **kwargs):
    """Archive the current implementation run and reset the workspace.

    Creates .implementations/implementation_N.zip from:
        plans/  .artifacts/

    Then clears those directories so the next run starts fresh.

    PATH is optional (default: current directory).
    """
    # Change to project directory so paths are relative to it
    original_cwd = os.getcwd()
    os.chdir(path)

    try:
        _do_archive()
    finally:
        os.chdir(original_cwd)


def _do_archive():
    """Perform the archive operation. Must be called from project directory."""
    DIRS_TO_ARCHIVE = [
        "./plans",
        "./.artifacts",
    ]
    IMPLEMENTATIONS_DIR = "./.implementations"

    # Find next implementation number
    os.makedirs(IMPLEMENTATIONS_DIR, exist_ok=True)
    existing = glob.glob(f"{IMPLEMENTATIONS_DIR}/implementation_*.zip")
    if not existing:
        n = 1
    else:
        numbers = []
        for zip_path in existing:
            name = os.path.splitext(os.path.basename(zip_path))[0]  # "implementation_3"
            try:
                numbers.append(int(name.split("_")[-1]))
            except ValueError:
                pass
        n = max(numbers) + 1 if numbers else 1

    zip_path = f"{IMPLEMENTATIONS_DIR}/implementation_{n}.zip"

    click.echo(f"Archiving to {zip_path} ...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dir_path in DIRS_TO_ARCHIVE:
            if not os.path.exists(dir_path):
                continue
            for root, _, files in os.walk(dir_path):
                for file in files:
                    full_path = os.path.join(root, file)
                    arcname = os.path.relpath(full_path, start=".")
                    zf.write(full_path, arcname)

    click.echo(f"Clearing source directories ...")
    for dir_path in DIRS_TO_ARCHIVE:
        if not os.path.exists(dir_path):
            continue
        for entry in os.listdir(dir_path):
            entry_path = os.path.join(dir_path, entry)
            if os.path.isfile(entry_path) or os.path.islink(entry_path):
                os.remove(entry_path)
            elif os.path.isdir(entry_path):
                shutil.rmtree(entry_path)

    click.echo(f"Done. Implementation {n} archived to {zip_path}.")
    click.echo("\n✅ Workspace reset! Start fresh with 'harness run'.")


@cli.command()
@click.argument("path", default=".", required=False, callback=_resolve_project_dir)
def monitor(path: Path):
    """Launch the live monitoring dashboard for a running harness.

    PATH is optional (default: current directory).
    """
    original_cwd = os.getcwd()
    os.chdir(path)
    try:
        import live_monitoring
        asyncio.run(live_monitoring.main())
    except KeyboardInterrupt:
        click.echo("\n👋 Monitor stopped.")
    finally:
        os.chdir(original_cwd)


if __name__ == "__main__":
    cli()
