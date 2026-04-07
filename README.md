# Claude Autonomous Harness

An AI-powered development workflow orchestration tool that uses Claude to execute software development tasks through iterative planning, execution, and review phases.

## Table of Contents

- [Overview](#overview)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [CLI Commands](#cli-commands)
  - [harness init](#harness-init-project_dir)
  - [harness run](#harness-run-options)
  - [harness status](#harness-status-options)
  - [harness clean](#harness-clean-options)
  - [harness monitor](#harness-monitor-options)
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
# 1. Initialize a project
harness init ./my-project

# 2. Edit the initial plan
vim ./my-project/plans/initial_plan.md

# 3. Run the harness
harness run -C ./my-project
```

## CLI Commands

All commands accept `-C, --project-dir DIR` to specify the target project directory. If omitted, it defaults to the current directory (`.`).

This means you can run `harness run` from **any** folder as long as the current directory (or `-C` path) contains a harness-initialized project.

### `harness init <project_dir>`

Initialize a project with the required directory structure:

```
plans/                  # Plan files (initial_plan.md, phase_*_plan.md)
.artifacts/             # Runtime artifacts (reports, memory, status)
.logs/                  # Log files
.archived_memory/       # Archived planning memory
.archived_artifacts/    # Archived execution artifacts
```

### `harness run [options]`

Run the harness on a project.

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `-C, --project-dir` | `.` | Project directory |
| `--mode` | `full` | `planning`, `execution`, or `full` |
| `--sub-agents` | `1` | Max concurrent worker agents |
| `--max-loops` | `3` | Max execution loops per phase |
| `--max-turns` | `15` | Max tool turns per Claude session |
| `--hourly-limit` | `20` | Max program calls per hour before cooldown |
| `--autonomous` | attended | Run in unattended mode |

**Examples:**

```bash
# Full run (planning + execution)
harness run -C ./my-project

# Planning only
harness run -C ./my-project --mode planning

# Execution only (requires existing plan)
harness run -C ./my-project --mode execution

# Autonomous mode (no human intervention)
harness run -C ./my-project --autonomous

# High parallelism with more loops
harness run -C ./my-project --sub-agents 4 --max-loops 5
```

### `harness status [options]`

Show current execution status:

```bash
harness status -C ./my-project
```

### `harness clean [options]`

Clear all harness state (artifacts, logs, archived files):

```bash
harness clean -C ./my-project
```

### `harness monitor [options]`

Launch a live monitoring dashboard that displays:
- **Left panel** — Live log stream from `orchestrator.log`
- **Right top** — Status overview (program calls, circuit breaker, exit gate, rate limit)
- **Right bottom** — Active workers

```bash
harness monitor -C ./my-project
```

The monitor polls `.artifacts/status.json` and tails `.logs/orchestrator.log` at 2 Hz.

## Project Structure

Each project managed by the harness has:

```
project/
├── plans/
│   ├── initial_plan.md           # Your initial plan
│   ├── phase_*_plan.md           # Generated phase plans
│   ├── planning_memory.md        # Planning phase memory
│   └── execution_state.json     # Execution resume state
│
├── .artifacts/
│   ├── phase_*_memory.md         # Per-phase execution memory
│   ├── phase_*_report.md         # Per-phase completion reports
│   ├── status.json               # Current orchestration status
│   └── rate_limiter_state.json   # Rate limiter state
│
└── .logs/
    └── orchestrator.log          # Execution logs
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
# Direct Python execution
python cli.py run -C ./my-project

# Or use main.py (legacy, only works in harness directory)
python main.py --mode full
```

## Configuration

Default settings can be modified in `config.py`:

```python
MODEL_ORCHESTRATOR = "claude-sonnet-4-6"    # Heavy reasoning tasks
MODEL_UTILITY = "claude-haiku-4-5"          # Cheap tasks (summaries)
N_SUB_AGENTS = 1                            # Concurrent workers
N_MAX_LOOPS = 3                              # Loops per phase
MAX_TURNS = "15"                             # Tool turns per session
HOURLY_PROGRAM_LIMIT = 20                       # program calls per hour
```
