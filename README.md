# Claude Autonomous Harness

An AI-powered development workflow orchestration tool that uses Claude to execute software development tasks through iterative planning, execution, and review phases.

## Table of Contents

- [Overview](#overview)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [CLI Commands](#cli-commands)
  - [harness init](#harness-init-path)
  - [harness run](#harness-run-path-options)
  - [harness status](#harness-status-path)
  - [harness clean](#harness-clean-path)
  - [harness archive](#harness-archive-path)
  - [harness monitor](#harness-monitor-path)
- [Project Structure](#project-structure)
- [Initial Plan Format](#initial-plan-format)
- [How It Works](#how-it-works)
- [Running Without Installation](#running-without-installation)
- [Configuration](#configuration)

## Overview

The harness operates in two phases:

1. **Planning Phase** — Refines the initial plan into concrete phases with KPIs
2. **Execution Phase** — Executes each phase iteratively until KPIs are met

## Installation

```bash
pip install -e /path/to/claude_autonomous_harness
```

Requires:
- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated

## Quick Start

```bash
# 1. Initialize a project (in current directory)
harness init

# 2. Edit the initial plan
vim plans/initial_plan.md

# 3. Run the harness
harness run
```

## CLI Commands

All commands operate on the current directory by default. Optionally, specify a project path as the first argument.

### `harness init [PATH]`

Initialize a project with the required directory structure. Defaults to current directory.

```
plans/                  # Plan files (initial_plan.md, phase_*_plan.md)
.artifacts/             # Artifact storage
  live_artifacts/       # Runtime artifacts (reports, memory, status)
  archived_artifacts/    # Archived execution artifacts
  archived_memory/      # Archived planning memory
.logs/                  # Log files
```

**Examples:**

```bash
harness init                       # Initialize in current directory
harness init ./my-project         # Initialize in specific directory
```

### `harness run [PATH] [options]`

Run the harness on a project. Defaults to current directory.

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--mode` | `full` | `planning`, `execution`, or `full` |
| `--sub-agents` | `1` | Max concurrent worker agents |
| `--max-loops` | `3` | Max execution loops per phase |
| `--max-turns` | `15` | Max tool turns per Claude session |
| `--hourly-limit` | `20` | Max program calls per hour before cooldown |
| `--autonomous` | attended | Run in unattended mode |

**Examples:**

```bash
harness run                        # Full run on current directory
harness run ./my-project           # Full run on specific directory
harness run --mode planning         # Planning only
harness run --mode execution        # Execution only (requires existing plan)
harness run --autonomous            # Autonomous mode (no human intervention)
harness run --sub-agents 4 --max-loops 5  # High parallelism
```

### `harness status [PATH]`

Show current execution status.

```bash
harness status                       # Status of current directory
harness status ./my-project         # Status of specific directory
```

### `harness clean [PATH]`

Clear all harness state (artifacts, logs).

```bash
harness clean                       # Clean current directory
harness clean ./my-project          # Clean specific directory
```

### `harness archive [PATH]`

Archive the current implementation run and reset the workspace. Creates `.implementations/implementation_N.zip` from `plans/` and `.artifacts/`, then clears them for a fresh start.

```bash
harness archive                      # Archive and reset current directory
harness archive ./my-project        # Archive and reset specific directory
```

### `harness monitor [PATH]`

Launch a live monitoring dashboard that displays:
- **Left panel** — Live log stream from `orchestrator.log`
- **Right top** — Status overview (program calls, circuit breaker, exit gate, rate limit)
- **Right bottom** — Active workers

```bash
harness monitor                      # Monitor current directory
harness monitor ./my-project        # Monitor specific directory
```

The monitor polls `.artifacts/live_artifacts/status.json` and tails `.logs/orchestrator.log` at 2 Hz.

## Project Structure

Each project managed by the harness has:

```
project/
├── plans/
│   ├── initial_plan.md           # Your initial plan
│   ├── phase_*_plan.md           # Generated phase plans
│   ├── planning_memory.md        # Planning phase memory
│   └── execution_state.json      # Execution resume state
│
├── .artifacts/
│   ├── live_artifacts/
│   │   ├── phase_*_memory.md         # Per-phase execution memory
│   │   ├── phase_*_report.md         # Per-phase completion reports
│   │   ├── status.json               # Current orchestration status
│   │   └── rate_limiter_state.json   # Rate limiter state
│   ├── archived_artifacts/           # Archived execution artifacts
│   └── archived_memory/              # Archived planning memory
│
└── .logs/
    └── orchestrator.log              # Execution logs
```

## Initial Plan Format

```markdown
# Initial Plan

## Objective
Describe what you want to accomplish.

## Scope
- Feature 1
- Feature 2

## KPIs
- [ ] KPI 1: Description
- [ ] KPI 2: Description

## Notes
Any additional context or constraints.
```

## How It Works

### Execution Flow

1. **Phase Loop** — Each phase runs in a loop until KPIs are met or max loops reached
2. **Worker Delegation** — Tasks are delegated to worker agents (in multi-agent mode)
3. **KPI Review** — Orchestrator reviews work against KPIs after each iteration
4. **Circuit Breaker** — Detects stuck loops and applies cooldown
5. **Exit Gate** — Determines when a phase is complete

### Safeguards

- **Circuit Breaker** — Opens after 5 loops with no progress, triggers cooldown
- **Exit Gate** — Requires both heuristic signals AND KPI confirmation to exit
- **Rate Limiter** — Cooldown when API call limit is reached
- **Git Commits** — Each phase commits with an orchestrator-generated message

## Running Without Installation

```bash
# Direct Python execution (from project directory)
python -m cli run

# Or from harness directory
python cli/__main__.py run .
```

## Configuration

Default settings can be modified in `config.py`:

```python
MODEL_ORCHESTRATOR = "claude-sonnet-4-6"    # Heavy reasoning tasks
MODEL_UTILITY = "claude-haiku-4-5"          # Cheap tasks (summaries)
N_SUB_AGENTS = 1                            # Concurrent workers
N_MAX_LOOPS = 3                              # Loops per phase
MAX_TURNS = "15"                             # Tool turns per session
HOURLY_PROGRAM_LIMIT = 20                    # program calls per hour
```
