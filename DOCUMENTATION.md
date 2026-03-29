# AMA Orchestrator — Complete Codebase Documentation

> **Who this is for:** Someone new to programming, or familiar with Python basics but not
> with asynchronous code, AI APIs, or system design patterns.
>
> **Reading tip:** Work through the sections in order. Section 3 ("Key Concepts") is
> especially important — the rest of the document assumes you have read it.

---

## Table of Contents

1. [What Does This System Do?](#1-what-does-this-system-do)
2. [The Big Picture: How Everything Connects](#2-the-big-picture-how-everything-connects)
3. [Key Concepts You Need to Know First](#3-key-concepts-you-need-to-know-first)
4. [Part 1 — The Orchestrator (`ama_orchestrator.py`)](#4-part-1--the-orchestrator-ama_orchestratorpy)
5. [Part 2 — The Rate Limiter (`ama_safeguards/rate_limiter.py`)](#5-part-2--the-rate-limiter-ama_safeguardsrate_limiterpy)
6. [Part 3 — The Circuit Breaker (`ama_safeguards/circuit_breaker.py`)](#6-part-3--the-circuit-breaker-ama_safeguardscircuit_breakerpy)
7. [Part 4 — The Exit Gate (`ama_safeguards/exit_gate.py`)](#7-part-4--the-exit-gate-ama_safeguardsexit_gatepy)
8. [Part 5 — The Status Writer (`ama_safeguards/status_writer.py`)](#8-part-5--the-status-writer-ama_safeguardsstatus_writerpy)
9. [Part 6 — The Package Entry Point (`ama_safeguards/__init__.py`)](#9-part-6--the-package-entry-point-ama_safeguardsinitpy)
10. [Part 7 — The Monitor (`ama_monitor.py`)](#10-part-7--the-monitor-ama_monitorpy)
11. [Part 8 — The Tests (`tests/`)](#11-part-8--the-tests-tests)
12. [Directory Structure & Artifacts Reference](#12-directory-structure--artifacts-reference)
13. [Configuration Reference](#13-configuration-reference)
14. [Glossary](#14-glossary)

---

## 1. What Does This System Do?

This project is an **autonomous coding assistant** that can plan and implement multi-step
software tasks by itself, using the Claude AI as its "brain."

Imagine you have a complicated software project to build. Instead of you doing every step
manually, you describe what you want in a plain-English plan file. The system then:

1. **Reads your plan** and (optionally) refines it with you.
2. **Breaks the plan into phases** — e.g., "Phase 1: build the database layer", "Phase 2:
   build the API".
3. **For each phase, spawns multiple AI worker agents** that run simultaneously and write
   code, run tests, and report back.
4. **Monitors all three safeguards** to make sure it doesn't spin out of control:
   - A **Rate Limiter** — stops it calling the AI API too many times per hour.
   - A **Circuit Breaker** — stops it if it keeps trying the same thing without progress.
   - An **Exit Gate** — only lets a phase finish when both the AI and measurable evidence
     agree the work is done.
5. **Writes a live dashboard** to your terminal so you can watch what is happening in
   real time.

The name **AMA** stands for **Autonomous Multi-Agent**.

---

## 2. The Big Picture: How Everything Connects

```
  You write: ama_plans/initial_plan.md
         │
         ▼
  ┌─────────────────────────────────────────────────────────┐
  │                  ama_orchestrator.py                    │
  │                                                         │
  │  Phase 0 (Planning)                                     │
  │    Master Claude reads plan → asks workers to research  │
  │    → you approve → phase files written to ama_plans/    │
  │                                                         │
  │  Execution Loop (per phase file):                       │
  │    ┌─────────────────────────────────────────────────┐  │
  │    │  Master Claude plans tasks for this loop        │  │
  │    │          │                                      │  │
  │    │          ▼  (up to 3 at once)                   │  │
  │    │  Worker 1 ──┐                                   │  │
  │    │  Worker 2 ──┼── each calls Claude CLI           │  │
  │    │  Worker 3 ──┘   writes code / runs tests        │  │
  │    │          │                                      │  │
  │    │          ▼                                      │  │
  │    │  Master Claude reviews KPIs                     │  │
  │    │          │                                      │  │
  │    │  ┌───────┴───────────────────────────────┐      │  │
  │    │  │         Three Safeguards check:        │      │  │
  │    │  │  RateLimiter  CircuitBreaker  ExitGate │      │  │
  │    │  └───────┬───────────────────────────────┘      │  │
  │    │          │                                      │  │
  │    │     Continue? Break? Wait? Raise?               │  │
  │    └─────────────────────────────────────────────────┘  │
  │                                                         │
  │  Writes ama_artifacts/status.json every loop            │
  └─────────────────────────────────────────────────────────┘
         │
         │ status.json + ama_logs/orchestrator.log
         ▼
  ┌────────────────────┐
  │   ama_monitor.py   │  ← run in a second terminal
  │  (live dashboard)  │
  └────────────────────┘
```

**Files on disk that connect the pieces:**

| File | Written by | Read by |
|---|---|---|
| `ama_plans/initial_plan.md` | You | Orchestrator |
| `ama_plans/phase_N_plan.md` | Master Claude | Orchestrator execution loop |
| `ama_artifacts/phase_N_memory.md` | Master Claude | Orchestrator (next loop) |
| `ama_artifacts/status.json` | StatusWriter | Monitor, crash recovery |
| `ama_artifacts/worker_L_N_stdout.txt` | Worker agents | Debugging, CircuitBreaker |
| `ama_logs/orchestrator.log` | Python `logging` | Monitor |
| `ama_artifacts/rate_limiter_state.json` | RateLimiter | RateLimiter (next run) |
| `ama_artifacts/circuit_breaker_state.json` | CircuitBreaker | CircuitBreaker (next run) |

---

## 3. Key Concepts You Need to Know First

Before reading the code, you need to understand a handful of ideas. None of these require
prior experience — they are explained from scratch below.

---

### 3.1 What is a "subprocess"?

Your Python script can launch other programs (like any command you would type in a terminal)
and capture their output. This is called running a **subprocess**.

In this project, the orchestrator launches the `claude` command-line tool as a subprocess.
It is exactly like typing `claude -p "your prompt"` in your terminal, except Python does it
automatically and reads the output.

```python
# This is what it looks like in the code:
process = subprocess.Popen(["claude", "-p", prompt, ...])
stdout, stderr = process.communicate()   # wait for it to finish, collect output
```

`stdout` is everything the program printed normally. `stderr` is where error messages go.

---

### 3.2 What is asynchronous programming?

**The restaurant analogy:**

Imagine a waiter in a busy restaurant. If the waiter is "synchronous", they take your order,
walk to the kitchen, stand there and watch the chef cook, then bring the food back, then take
the next table's order. The waiter is doing one thing at a time and wasting a lot of time
standing around waiting.

If the waiter is "asynchronous", they take your order, drop it off at the kitchen, then walk
to another table to take their order, then check on a third table — all while the kitchen is
cooking. When the food is ready, the waiter picks it up and brings it over.

Python's `asyncio` library gives you the asynchronous waiter. The key words to recognise:

| Keyword | What it means |
|---|---|
| `async def` | "This function is like the async waiter — it can pause and let others run." |
| `await` | "Pause here and let other things run until this is ready." |
| `asyncio.gather(...)` | "Start all these tasks at once and wait until they're all done." |
| `asyncio.sleep(n)` | "Do nothing for n seconds, but let other tasks run in the meantime." |

**Why does this project use async?**

Worker agents call the Claude CLI, and each call can take 30–120 seconds. If the code were
synchronous (one at a time), running 3 workers would take 3× as long. With `asyncio`, all
three workers run simultaneously and the total time is roughly the time of the slowest one.

---

### 3.3 What is a Semaphore?

A **semaphore** is a counter that controls how many things can happen at once.

Think of a parking lot with 3 spaces. When a car enters, the counter goes from 3 to 2.
When another car leaves, it goes back up. If the lot is full (counter = 0), the next car
must wait.

```python
sem = asyncio.Semaphore(3)   # Only 3 workers may run at the same time

async with sem:              # "Take a parking space"
    # ... do work ...
    pass
# "Space is automatically freed when we exit the 'with' block"
```

In this project, `X1_MAX_WORKERS = 3` means at most 3 worker agents can call Claude
simultaneously.

---

### 3.4 What is a "Rate Limit"?

AI APIs charge money per call and have limits on how many calls you can make per hour.
If you exceed this, the API returns an error (HTTP 429 "Too Many Requests") and makes you
wait before trying again.

A **rate limiter** in software is a gatekeeper that counts how many calls have been made
and refuses to let more go through until enough time has passed.

---

### 3.5 What is a "Circuit Breaker"?

The term comes from electrical engineering. An electrical circuit breaker "trips" (opens)
when it detects a dangerous condition like a short circuit, cutting power to prevent damage.
Once the problem is fixed, you reset it.

In software, a circuit breaker detects when the system is in a bad state — specifically,
when it keeps doing work but making no progress (stuck in a loop) — and "opens" to stop
further calls until a cooldown period has passed.

The three states:

```
CLOSED ──── too many failed loops ───► OPEN
  ▲                                      │
  │                                cooldown elapsed
  │                                      │
  └────── progress detected ──── HALF_OPEN (trial)
```

---

### 3.6 What is JSON?

**JSON** (JavaScript Object Notation) is a standard text format for storing structured data.
It looks like Python dictionaries:

```json
{
  "phase": "execution",
  "loop_count": 7,
  "kpis_met": false,
  "active_workers": [1, 3]
}
```

This project uses JSON files to save state to disk (so that if the program crashes and
restarts, it can pick up where it left off) and as the format for the status dashboard.

---

### 3.7 What are KPIs?

**KPIs (Key Performance Indicators)** are the specific, measurable goals for a phase.
Instead of "the feature is done", a KPI might be "the unit test `test_rate_limiter.py`
passes with 0 failures." The system uses KPIs to decide when a phase is genuinely complete.

---

## 4. Part 1 — The Orchestrator (`ama_orchestrator.py`)

This is the main file. It coordinates everything. Think of it as the "conductor" of the
orchestra.

### 4.1 Imports and Setup

```python
import asyncio        # for running workers simultaneously
import subprocess     # for launching the Claude CLI
import json           # for parsing Claude's structured responses
import re             # for pattern matching in text
import os             # for file/folder operations
import glob           # for finding files matching a pattern (e.g. "phase_*.md")
import logging        # for writing timestamped log messages
import sys            # for reading command-line arguments
import time           # for sleeping (pausing) the program

from ama_safeguards import CircuitBreaker, ExitGate, RateLimiter
from ama_safeguards.status_writer import write_status, register_worker, ...
```

The first block imports Python's standard library tools. The second block imports the
safeguard modules we built (covered in later sections).

```python
PATH_AMA_PLANS    = "./ama_plans"
PATH_AMA_ARTIFACTS = "./ama_artifacts"
PATH_LOGS         = "./ama_logs"

os.makedirs(PATH_AMA_PLANS, exist_ok=True)    # create the folder if it doesn't exist
os.makedirs(PATH_AMA_ARTIFACTS, exist_ok=True)
os.makedirs(PATH_LOGS, exist_ok=True)
```

These three directories are created at startup if they don't exist. `exist_ok=True` means
"don't crash if the folder already exists."

---

### 4.2 Configuration Constants

```python
X1_MAX_WORKERS = 3    # Never more than 3 workers running at once
N_MAX_LOOPS    = 5    # Give up on a phase after 5 loops even if KPIs aren't met
MAX_TURNS      = "15" # Max number of tool-use steps Claude can take in one call

UNATTENDED_MODE = os.environ.get("AMA_UNATTENDED", "0") == "1"
```

`os.environ.get("AMA_UNATTENDED", "0")` reads an **environment variable** — a setting you
can pass to the program from the terminal without changing the code:

```bash
AMA_UNATTENDED=1 python ama_orchestrator.py   # runs without any human prompts
AMA_UNATTENDED=0 python ama_orchestrator.py   # (default) pauses for human approval
```

When `UNATTENDED_MODE` is `True`, the system can run overnight without anyone at the
keyboard.

---

### 4.3 Interactive Prompt Intercept

```python
INTERACTIVE_PROMPT_PATTERNS = [
    re.compile(r"\(yes/no(/\[fingerprint\])?\)\s*\??$", re.IGNORECASE),
    re.compile(r"password\s*:", re.IGNORECASE),
    re.compile(r"enter passphrase", re.IGNORECASE),
    re.compile(r"please type 'yes', 'no' or the fingerprint", re.IGNORECASE),
]

_UNATTENDED_DEFAULTS = ["yes", "yes", "yes", "yes"]
```

A worker agent might run a tool like `git clone` that asks an interactive question:

```
Are you sure you want to continue connecting (yes/no)?
```

Because the worker subprocess's keyboard is not connected to your terminal, this question
would hang forever without an answer. The `INTERACTIVE_PROMPT_PATTERNS` list defines the
question patterns we watch for. When one is detected, the program either:
- **Asks you** to type an answer (attended mode), or
- **Automatically answers** "yes" (unattended mode).

`re.compile(...)` creates a **regular expression** — a mini-language for describing text
patterns. `re.IGNORECASE` means the pattern matches regardless of upper/lower case.

---

### 4.4 `_stream_with_intercept(process, worker_id)`

```python
async def _stream_with_intercept(process, worker_id: int) -> tuple[str, str]:
```

This function replaces the simpler `process.communicate()` call. Instead of waiting for
the entire subprocess to finish and then reading all output at once, it reads output
**line by line as it arrives** — like watching a live ticker tape.

**Why?** Because `process.communicate()` would hang forever if the subprocess asked a
question and waited for keyboard input. By reading line-by-line, we can spot the question
and answer it before the subprocess gets stuck.

```python
async def read_stream(stream, lines_buf: list[str]) -> None:
    async for line in stream:               # read one line at a time as it arrives
        text = line.decode("utf-8", ...).rstrip()
        lines_buf.append(text)
        for idx, pattern in enumerate(INTERACTIVE_PROMPT_PATTERNS):
            if pattern.search(text):        # does this line look like a question?
                # ... inject an answer ...
                break

await asyncio.gather(
    read_stream(process.stdout, stdout_lines),
    read_stream(process.stderr, stderr_lines),
)
```

`asyncio.gather` runs both `read_stream` calls simultaneously — one watching stdout and
one watching stderr — so neither blocks the other.

`line.decode("utf-8", errors="replace")` converts raw bytes (what the subprocess sends)
into a Python string. `errors="replace"` means "if there's a byte that isn't valid UTF-8
text, replace it with a placeholder instead of crashing."

---

### 4.5 Helper Functions

```python
def _get_baseline_commit() -> str:
```
Runs `git rev-parse HEAD` to get a unique identifier (hash) for the current state of the
code repository. This is captured at the start of each loop so we can later check whether
any files changed during that loop.

```python
def count_git_diff_files(baseline_commit: str) -> int:
```
Runs `git diff --name-only <baseline_commit>` to count how many files have been modified
since the start of this loop. Used by the circuit breaker to detect progress.

```python
def count_new_artifacts(loop_num: int) -> int:
```
Counts how many `worker_{loop_num}_*_stdout.txt` files exist. If workers ran, they each
created one of these files, so the count tells us how many workers produced output this loop.

```python
def extract_error_signature(outputs: list[str]) -> str | None:
```
Scans all worker report text for lines containing "error", "exception", "traceback", or
"failed". Returns the first such line (truncated to 200 characters) as an "error signature."
If the same error signature appears in 5 consecutive loops, the circuit breaker opens.

---

### 4.6 `run_worker_agent(sem, worker_id, task_prompt, loop_num, rate_limiter)`

This is an `async def` function because workers run simultaneously.

```python
async def run_worker_agent(sem, worker_id, task_prompt, loop_num=0, rate_limiter=None):
    async with sem:                          # wait for a semaphore slot (max 3 at once)
        if rate_limiter:
            while not rate_limiter.can_make_call():
                await asyncio.sleep(1)       # pause 1s, let other tasks run, then check again
            rate_limiter.record_call()

        await register_worker(worker_id, task_prompt)   # mark as "running" in registry
        try:
            process = await asyncio.create_subprocess_exec(
                "claude", "-p", full_prompt, "--dangerously-skip-permissions",
                "--max-turns", MAX_TURNS,
                stdin=asyncio.subprocess.PIPE,    # connected so we can send answers
                stdout=asyncio.subprocess.PIPE,   # captured so we can read output
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_text, stderr_text = await _stream_with_intercept(process, worker_id)

            # Save raw output to a file for debugging
            with open(f"ama_artifacts/worker_{loop_num}_{worker_id}_stdout.txt", "w") as f:
                f.write(stdout_text + "\n--- STDERR ---\n" + stderr_text)

            # Read the summary file the worker wrote
            output_file = f"ama_artifacts/worker_{worker_id}_output.md"
            result = "Worker completed, but no output file was found."
            if os.path.exists(output_file):
                with open(output_file, "r") as f:
                    result = f.read()
                os.remove(output_file)    # clean up after reading

            return f"--- WORKER {worker_id} REPORT ---\n{result}\n"
        finally:
            await deregister_worker(worker_id)   # always unmark, even if an error occurred
```

The `try: ... finally:` block guarantees that `deregister_worker` is called even if an
exception is raised mid-way. Without `finally`, a crashed worker would be stuck in the
registry forever, showing as "running" in the dashboard.

The workers receive a prompt like:
> *"You are Independent Worker 2. Execute the following task using your tools. When
> finished, write a brief summary to `ama_artifacts/worker_2_output.md` and exit."*

The worker then uses Claude's own tools (file editing, bash commands, etc.) to carry out
the task, and writes its report to the named file.

---

### 4.7 `run_orchestrator(prompt, require_json, rate_limiter)`

This is a regular (`def`, not `async def`) function because the orchestrator runs one at
a time — there is only one master Claude.

```python
def run_orchestrator(prompt, require_json=False, rate_limiter=None):
    # 1. Handle rate limiting
    if rate_limiter and not rate_limiter.can_make_call():
        wait_secs = rate_limiter.seconds_until_reset()
        time.sleep(wait_secs)           # synchronous sleep — this is already blocking code
        rate_limiter.clear_cooldown()
    if rate_limiter:
        rate_limiter.record_call()

    # 2. Optionally require JSON output
    if require_json:
        prompt += "\n\nIMPORTANT: You MUST output your response ONLY as a valid JSON block..."

    # 3. Launch Claude and wait for it to finish
    process = subprocess.Popen(["claude", "-p", prompt, ...])
    stdout, stderr = process.communicate()   # blocks until Claude is done

    # 4. Parse JSON if required
    if require_json:
        match = re.search(r'```json\s*(.*?)\s*```', stdout, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        return None

    return stdout
```

`require_json=True` is used when the orchestrator needs a structured answer (like a list
of tasks) rather than free-form text. We tell Claude to wrap its answer in a JSON code
block, then extract and parse it.

`re.search(r'```json\s*(.*?)\s*```', stdout, re.DOTALL)` is a regular expression that
looks for the pattern:
```json
{ ... }
```
`re.DOTALL` makes the `.` in the pattern match newlines too (so the JSON block can span
multiple lines).

---

### 4.8 `plan_refinement_phase()`

This is Phase 0 — the planning phase. It always requires human approval, even in
`UNATTENDED_MODE`. The flow:

1. Master Claude reads `initial_plan.md` and decides whether it needs research workers.
2. If research is needed, workers are launched to explore (docs, APIs, feasibility).
3. Master Claude writes the phase plan files (`phase_1_plan.md`, `phase_2_plan.md`, …).
4. Master Claude writes a risk assessment report — questions and concerns for the human.
5. **You** read the report and either type `approve` or type feedback.
6. If you give feedback, Master Claude updates the plans. The loop repeats from step 1.
7. Once you approve, execution begins.

---

### 4.9 `execution_phase()`

This is the main loop. It processes phase plan files one by one:

```
For each phase_N_plan.md:
    Reset the exit gate (fresh slate for this phase)
    Create a memory file for this phase

    loop_num = 1
    while loop_num <= N_MAX_LOOPS:
        1. Snapshot git HEAD (baseline commit)
        2. Ask Master Claude: "what tasks should workers do right now?" → JSON task list
        3. Launch worker agents for each task
        4. Ask Master Claude: "review the KPIs — are we done?" → JSON review
        5. Ask Master Claude to update the memory file
        6. Feed results to ExitGate
        7. Feed progress metrics to CircuitBreaker
        8. Write status.json (for the dashboard)
        9.  If CB is OPEN:
               - UNATTENDED: sleep the cooldown, then continue WITHOUT incrementing loop_num
               - ATTENDED:   raise an error and stop
       10. If ExitGate says exit: break out of the loop
       11. If ATTENDED and KPIs not met: ask the human for guidance
       12. Increment loop_num and continue

    Generate a phase completion report
```

**Why a `while` loop instead of a `for` loop?**

A `for loop_num in range(1, N_MAX_LOOPS+1)` always increments the counter. But when the
circuit breaker opens, we want to pause *without consuming a loop slot* — the system is
stuck, waiting for things to cool down. The `while` loop lets us use `continue` (which
jumps back to the top of the loop) before reaching the `loop_num += 1` line at the bottom.

---

### 4.10 `main()` and the Entry Point

```python
if __name__ == "__main__":
    async def main():
        if "--skip-planning" in sys.argv:
            # jump straight to execution (useful when plans already exist)
            pass
        else:
            await plan_refinement_phase()
        await execution_phase()

    asyncio.run(main())
```

`asyncio.run(main())` starts the async event loop and runs `main()` inside it. This is
the standard way to launch an async program in Python. Everything async in this project
ultimately traces back to this single call.

`if __name__ == "__main__"` means "only run this code when this file is executed directly,
not when it is imported by another file."

---

## 5. Part 2 — The Rate Limiter (`ama_safeguards/rate_limiter.py`)

### 5.1 What problem does it solve?

The Claude API allows roughly 10 calls per hour on typical plans. If the orchestrator
exceeds this limit, the API returns an error and makes you wait an hour. Without a rate
limiter, the system would crash or spin in an error loop burning your quota.

### 5.2 The "hour bucket" concept

The rate limiter divides time into one-hour windows. Each window has a label like
`"2026-03-30T14"` (meaning the hour starting at 14:00 UTC on March 30, 2026). The counter
resets to zero at the start of each new window.

```python
HOURLY_CALL_LIMIT = 10    # Max calls per hour window
STATE_FILE = "ama_artifacts/rate_limiter_state.json"
COOLDOWN_SECONDS = 3600   # 1 hour in seconds
```

### 5.3 How rate-limit signals are detected (two layers)

When Claude is overloaded or you've exceeded your quota, it returns a structured error.
The rate limiter checks for this in two ways:

**Layer 1 — Structural patterns** (checked in all of stdout):
```python
_STRUCTURAL_PATTERNS = [
    '"type": "rate_limit_event"',   # Claude's API error format
    '"is_error": true',             # another Claude API error indicator
]
```
This catches the exact JSON error messages the Claude CLI emits.

**Layer 2 — Text patterns** (checked only in the last 30 lines of combined output):
```python
_TEXT_PATTERNS = [
    "rate limit",
    "429",          # HTTP status code for "Too Many Requests"
    "overloaded",
]
```
The "last 30 lines" restriction is important: it prevents the system from triggering on a
*narrative* mention of rate limits in the middle of Claude's response (e.g., "the old
code had a rate limit bug..."). By only checking recent output, we focus on what Claude
said *at the end* — which is where error messages appear.

### 5.4 State persistence

```python
def _save_state(self) -> None:
    with open(self.state_file, "w") as f:
        json.dump(self._state, f, indent=2)
```

The state is saved to a JSON file after every change. This means if the program crashes and
restarts, the call counter is not lost — it loads from the file and continues counting.

```json
{
  "hour_bucket": "2026-03-30T14",
  "calls_this_hour": 7,
  "last_reset_ts": 1743350400.0,
  "rate_limit_cooldown_until": null
}
```

`last_reset_ts` is a Unix timestamp (number of seconds since January 1, 1970) — Python's
`time.time()` returns this format.

### 5.5 The `time_fn` parameter (for testing)

```python
def __init__(self, ..., time_fn=None):
    self._time_fn = time_fn if time_fn is not None else time.time
```

Every call to `time.time()` in the code is replaced by `self._time_fn()`. In normal use
this defaults to the real clock. In tests, we pass a fake clock function that we can
advance instantly without actually sleeping. This lets tests verify "what happens after 1
hour" in milliseconds.

### 5.6 Public methods summary

| Method | What it does |
|---|---|
| `can_make_call()` | Returns `True` if a call is allowed (limit not reached, no cooldown) |
| `record_call()` | Increments the counter and saves to disk |
| `record_rate_limit_signal()` | Sets a 1-hour cooldown starting now |
| `parse_output_for_limit(stdout, stderr)` | Returns `True` if output contains rate-limit signals |
| `seconds_until_reset()` | How many seconds until the cooldown expires |
| `clear_cooldown()` | Immediately removes the cooldown (used after waiting) |
| `wait_for_reset()` | `async` — sleeps until cooldown expires (or raises error in attended mode) |

---

## 6. Part 3 — The Circuit Breaker (`ama_safeguards/circuit_breaker.py`)

### 6.1 What problem does it solve?

A subtle failure mode of autonomous systems is the "stuck loop": the system keeps trying,
generating output, spending API calls, but making no real progress. This can happen if:
- The AI is confused and keeps doing the same wrong thing.
- A dependency is missing and every attempt fails with the same error.
- The task is genuinely impossible as stated.

The circuit breaker detects this and cuts off calls before you burn your entire API budget.

### 6.2 The three states

```
CLOSED ──[no progress × 3]──► OPEN ──[cooldown expires]──► HALF_OPEN
  ▲                                                              │
  │                                                         [try one loop]
  └──────────────── progress detected ────────────────────────────
```

- **CLOSED**: Normal operation. All calls go through.
- **OPEN**: Stuck detected. Calls are blocked. The system waits for `cooldown_seconds`
  (default: 1800 seconds = 30 minutes).
- **HALF_OPEN**: The cooldown has expired. One trial loop is allowed. If progress is
  detected, return to CLOSED. If still no progress, go back to OPEN and reset the timer.

### 6.3 What counts as "progress"?

```python
progress = files_changed > 0 or worker_artifacts_produced > 0 or kpi_advancement
```

Any one of these three means "something happened this loop":
- `files_changed`: at least one file in the git repository was modified.
- `worker_artifacts_produced`: at least one `worker_stdout.txt` file was created.
- `kpi_advancement`: Master Claude says at least one new KPI was satisfied.

### 6.4 The same-error rule

Even if files are changing, if every loop fails with *exactly the same error*, the system
is spinning. 5 identical error signatures in a row will also trip the breaker:

```python
if error_signature == self._last_error_signature:
    self._consecutive_same_error += 1
```

An "error signature" is the first error-like line found in the worker output, truncated
to 200 characters. It serves as a fingerprint for the type of failure.

### 6.5 State file

```json
{
  "state": "CLOSED",
  "consecutive_no_progress": 0,
  "consecutive_same_error": 0,
  "last_error_signature": null,
  "opened_at": null,
  "cooldown_seconds": 1800
}
```

`opened_at` is the Unix timestamp when the circuit was tripped. `check_cooldown()` computes
`time.now() - opened_at` and transitions to HALF_OPEN when the difference exceeds
`cooldown_seconds`.

### 6.6 Public methods summary

| Method | What it does |
|---|---|
| `is_open()` | `True` if the breaker is OPEN (calls blocked) |
| `get_state()` | Returns `"CLOSED"`, `"OPEN"`, or `"HALF_OPEN"` as a string |
| `record_loop_result(...)` | Feed one loop's progress data; internally transitions state |
| `check_cooldown()` | Transitions OPEN → HALF_OPEN if cooldown elapsed; returns `True` if it did |
| `cooldown_seconds` | Property: how long (in seconds) the OPEN state waits before allowing a trial |

---

## 7. Part 4 — The Exit Gate (`ama_safeguards/exit_gate.py`)

### 7.1 What problem does it solve?

When the orchestrator asks Master Claude "are all KPIs met?", Claude might say "yes" even
when things aren't quite right — AI models can be overconfident. Conversely, Claude might
say "no" even when the code is actually working, because it can't run the tests perfectly.

The exit gate enforces a **dual confirmation rule**: a phase only ends when *both* of the
following are true:

1. **AI confirmation**: Master Claude explicitly says `kpis_met: true`.
2. **Heuristic confirmation**: Worker outputs contain at least 2 distinct completion
   keywords (e.g. "task complete", "all tests pass", "done").

Neither alone is sufficient. Both must agree.

### 7.2 The heuristic scoring system

The gate looks for these completion keywords in worker output text:

```python
_COMPLETION_KEYWORDS = [
    "task complete",
    "all tests pass",
    "kpis met",
    "finished",
    "done",
]
```

To avoid false positives (like "the migration was not **done** because of a bug"), a keyword
only counts if it appears in one of two ways:

1. **At the start of a line**: `^\s*done\b` — the line begins with the word (after optional
   spaces), followed by a word boundary. So "done." counts, but "undone" does not.
2. **Inside a relevant section**: If the text has a heading containing "summary", "result",
   or "status", any keyword on the lines below that heading counts.

Each distinct keyword found adds +1 to `heuristic_score`, capped at 5.

### 7.3 The safety breaker

What if neither condition is ever fully met but the system keeps getting close? After 5
consecutive loops where *any* completion keywords are found in worker output, the gate
forces an exit with a WARNING log — preventing an infinite "almost done" loop.

```python
if self._state.consecutive_completion_signals >= 5:
    logger.warning("ExitGate safety breaker ... forcing exit")
    return True
```

### 7.4 The `ExitGateState` dataclass

```python
@dataclass
class ExitGateState:
    consecutive_completion_signals: int = 0
    kpis_met_confirmed: bool = False
    heuristic_score: int = 0
```

A `dataclass` is a Python shortcut for creating a class that is mainly a container for
data. The `= 0` and `= False` are default values.

### 7.5 Public methods summary

| Method | What it does |
|---|---|
| `should_exit()` | `True` when both conditions are met (or safety breaker fires) |
| `record_worker_outputs(outputs)` | Parse worker text and update heuristic score |
| `record_kpi_review(kpis_met)` | Record Master Claude's KPI verdict |
| `reset()` | Clear all state (called between phases) |
| `get_state()` | Return a snapshot of the current state (for StatusWriter) |
| `restore_state(state)` | Reload state from a snapshot (crash recovery) |

---

## 8. Part 5 — The Status Writer (`ama_safeguards/status_writer.py`)

### 8.1 What does it do?

At the end of every execution loop, this module writes a single JSON file:

```
ama_artifacts/status.json
```

The monitor (`ama_monitor.py`) reads this file every second to update the dashboard. This
is how a program running in one terminal can display live information in another terminal —
they communicate through a shared file.

### 8.2 The Worker Registry

```python
_worker_registry: dict[int, str] = {}   # worker_id → task description
_registry_lock = asyncio.Lock()
```

This is a **module-level variable** — a single shared object that exists for the lifetime
of the program. All parts of the code that import this module share the same
`_worker_registry` dictionary.

An `asyncio.Lock()` is like a mutex — it ensures that only one async task can modify the
dictionary at a time, preventing two workers from simultaneously trying to update the same
data structure (which could cause corruption).

```python
async def register_worker(worker_id: int, task: str) -> None:
    async with _registry_lock:      # "acquire the lock — nobody else can enter while I'm here"
        _worker_registry[worker_id] = task
    # lock is automatically released when we leave the "with" block
```

`async with` is the async version of `with` — it acquires the lock in a non-blocking way
(other tasks can run while this task waits for the lock, if it's currently held by another
task).

### 8.3 `write_status(...)` function

```python
def write_status(
    phase, loop_count, api_calls_this_hour,
    circuit_breaker_state, exit_gate_heuristic,
    exit_gate_kpis_met, active_workers,
    rate_limit_cooldown_until,
    status_file=STATUS_FILE,
) -> None:
```

Gathers all the current state into one dictionary and writes it as JSON. The `updated_at`
timestamp is added automatically:

```python
"updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
```

`datetime.now(timezone.utc)` gets the current time in UTC (Coordinated Universal Time —
the standard timezone used in computing). `.strftime(...)` formats it as a string like
`"2026-03-30T14:32:00Z"` — an internationally standard format called ISO 8601.

---

## 9. Part 6 — The Package Entry Point (`ama_safeguards/__init__.py`)

```python
from ama_safeguards.circuit_breaker import CircuitBreaker
from ama_safeguards.exit_gate import ExitGate, ExitGateState
from ama_safeguards.rate_limiter import RateLimiter, RateLimitError
from ama_safeguards.status_writer import (
    write_status, register_worker, deregister_worker, get_active_workers,
)

__all__ = [...]
```

A folder containing an `__init__.py` file is a **Python package**. When you write
`from ama_safeguards import RateLimiter`, Python finds the `ama_safeguards` folder,
runs `__init__.py`, and looks for `RateLimiter` in its namespace.

By importing all the useful names *into* `__init__.py`, the orchestrator can write:
```python
from ama_safeguards import CircuitBreaker, ExitGate, RateLimiter
```
instead of the more verbose:
```python
from ama_safeguards.circuit_breaker import CircuitBreaker
from ama_safeguards.exit_gate import ExitGate
from ama_safeguards.rate_limiter import RateLimiter
```

`__all__` is a list of names that are exported when someone writes
`from ama_safeguards import *`. It is a documentation hint — it does not enforce
anything, but it clearly communicates "these are the intended public exports."

---

## 10. Part 7 — The Monitor (`ama_monitor.py`)

### 10.1 What does it do?

This is a standalone script you run in a **second terminal window** while the orchestrator
runs in the first. It provides a live, full-screen terminal dashboard using the
[Rich](https://rich.readthedocs.io/) library — no browser, no web server required.

```bash
# Terminal 1:
AMA_UNATTENDED=1 .venv/bin/python ama_orchestrator.py --skip-planning

# Terminal 2:
.venv/bin/python ama_monitor.py
```

### 10.2 Layout structure

```
┌── header (3 lines tall) ──────────────────────────────────────────┐
│  AMA Orchestrator Monitor  │  Phase: phase_2  │  Loop: 3  │ 14:32  │
├── left panel (60% width) ──┬── right panel (40% width) ──────────┤
│                            │  Status Overview                     │
│  Live Log Stream           │  API calls:  7 / 10  [▓▓▓▓▓▓▓▓▓▓░░]  │
│                            │  Circuit:    CLOSED ✅               │
│  14:32:01 🚀 Worker 1 ...  │  Exit gate:  heuristic=1/2  KPI=✗   │
│  14:32:02 🧠 Orch thinking │  Rate limit: OK                      │
│  14:32:05 ✅ Worker 3 done │                                      │
│                            ├── Active Workers ────────────────────┤
│                            │  Worker 1  running                   │
│                            │  Worker 3  running                   │
└────────────────────────────┴──────────────────────────────────────┘
```

Rich's `Layout` object is like a grid system. We split the screen top-to-bottom into a
header and a body, then split the body left-to-right into a log panel and a status area,
then split the status area top-to-bottom into a status panel and a workers panel.

### 10.3 How `Live` works (and why it's not `async`)

```python
with Live(layout, screen=True, refresh_per_second=REFRESH_HZ):
    await asyncio.gather(
        tail_log(layout),
        poll_status(layout),
    )
```

`Live` uses a background *thread* (separate from the async event loop) to redraw the
screen at `REFRESH_HZ` times per second. Our two async tasks (`tail_log` and `poll_status`)
update the `layout` object, and `Live`'s thread reads from it to draw.

**Important:** `Live` is a regular synchronous context manager (`with`, not `async with`).
You CAN use a synchronous `with` inside an `async def` function — they are compatible.
What you cannot do is use `async with Live(...)` because Rich's `Live` class does not
implement the async context manager protocol.

### 10.4 `tail_log(layout)` — the log watcher

```python
async def tail_log(layout: Layout) -> None:
    last_size: int = -1
    while True:
        if LOG_FILE.exists():
            size = LOG_FILE.stat().st_size    # how many bytes is the file right now?
            if size != last_size:             # has it grown since we last checked?
                last_size = size
                raw = LOG_FILE.read_text(errors="replace")
                lines = raw.splitlines()[-LOG_TAIL:]    # keep only the last 30 lines
        layout["left"].update(build_log_panel(lines))
        await asyncio.sleep(0.5)              # wait half a second, let other tasks run
```

`Path.stat().st_size` is an efficient way to detect file changes — checking a file's size
is much faster than reading the entire file every time. We only re-read the file when its
size has changed.

### 10.5 `poll_status(layout)` — the status watcher

```python
async def poll_status(layout: Layout) -> None:
    while True:
        status: dict = {}
        try:
            if STATUS_FILE.exists():
                raw = STATUS_FILE.read_text(errors="replace")
                status = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            pass    # keep previous state if the file is momentarily corrupt or missing
        layout["header"].update(build_header(status))
        layout["status"].update(build_status_panel(status))
        layout["workers"].update(build_workers_panel(status))
        await asyncio.sleep(1.0)
```

The `try/except` block handles two failure cases gracefully:
- `OSError`: the file doesn't exist or can't be read.
- `json.JSONDecodeError`: the orchestrator is in the middle of writing the file, so we
  caught it with partial content that isn't valid JSON yet.

In both cases, `pass` means "do nothing" — the panels keep showing the last valid state.

### 10.6 Panel color coding

```python
def _log_line_style(line: str) -> str:
    if "[ERROR]" in line or "❌" in line:  return "red"
    if "[WARNING]" in line or "🚦" in line: return "yellow"
    if "✅" in line or "🎉" in line:        return "green"
    return "white"
```

Log lines are colored by their severity. The emoji checks (`🚦`, `⚡`) complement the
text markers because the orchestrator uses them in log messages too.

For the circuit breaker state:
```python
if cb_state == "OPEN":
    text.append("OPEN ⚡\n", style="bold red")    # prominent warning
elif cb_state == "CLOSED":
    text.append("CLOSED ✅\n", style="green")     # all good
elif cb_state == "HALF_OPEN":
    text.append("HALF_OPEN ⚠️\n", style="yellow")  # caution
```

### 10.7 Graceful shutdown

```python
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass    # Rich's Live.__exit__ has already restored the terminal
```

When you press `Ctrl+C`:
1. Python raises `KeyboardInterrupt`.
2. `asyncio.run()` catches it, cancels all running tasks (sending them a
   `CancelledError`).
3. The `with Live(...)` block's `__exit__` method is called, which switches the terminal
   back from "alternate screen" (full-screen Rich) to normal.
4. The `except` block in `__main__` catches the `KeyboardInterrupt` so no ugly traceback
   is printed.

---

## 11. Part 8 — The Tests (`tests/`)

The tests verify each safeguard works correctly in isolation, without needing a running
Claude API or a real terminal.

### 11.1 Test isolation principle (KPI-4.5)

Every test that uses disk state creates its own **temporary directory** and cleans it up
when done:

```python
def setUp(self):
    self.tmp_dir = tempfile.mkdtemp()    # create e.g. /tmp/tmpXYZ123/

def tearDown(self):
    shutil.rmtree(self.tmp_dir, ignore_errors=True)    # delete it when done
```

`setUp` runs before each test method. `tearDown` runs after, even if the test fails.
This ensures no test leaves files behind that could affect other tests.

### 11.2 `tests/test_rate_limiter.py` (11 tests)

Key techniques:
- **Fake clock**: `t = [time.time()]; time_fn = lambda: t[0]` — a list is used so the
  lambda can access and the test code can modify the same value. Lists are mutable; a plain
  variable captured in a lambda cannot be reassigned from outside.
- **Advance clock**: `t[0] += 3601` — simulates one hour passing instantly.

```python
def test_reset_on_new_hour(self):
    t = [time.time()]
    rl = RateLimiter(limit=100, state_file=..., time_fn=lambda: t[0])
    for _ in range(100):
        rl.record_call()
    self.assertFalse(rl.can_make_call())    # full

    t[0] += 3601                            # advance clock by 1 hour + 1 second
    self.assertTrue(rl.can_make_call())     # bucket rolled over, counter reset
```

### 11.3 `tests/test_circuit_breaker.py` (9 tests)

```python
def test_half_open_after_cooldown(self):
    t = [time.time()]
    cb = CircuitBreaker(state_path=..., cooldown_seconds=100, time_fn=lambda: t[0])

    for _ in range(3):
        cb.record_loop_result(0, 0, False, None)   # 3× no progress → OPEN

    t[0] += 101                     # advance clock past the 100s cooldown
    result = cb.check_cooldown()    # should detect elapsed cooldown

    self.assertTrue(result)
    self.assertEqual(cb.get_state(), "HALF_OPEN")
```

Note the `test_different_errors_dont_trigger` test uses `files_changed=1`:

```python
cb.record_loop_result(
    files_changed=1,          # progress present — only testing the same-error rule
    worker_artifacts_produced=0,
    kpi_advancement=False,
    error_signature=f"unique_err_{i}",
)
```

This is deliberate. Without `files_changed=1`, the no-progress counter would reach 3 and
trip the breaker before the same-error counter could be tested independently.

### 11.4 `tests/test_exit_gate.py` (7 tests)

ExitGate has no disk I/O, so no temp directories are needed. All state is in memory.

```python
def test_safety_breaker_5_signals(self):
    self.gate.restore_state(
        ExitGateState(consecutive_completion_signals=5, ...)
    )
    with self.assertLogs(level=logging.WARNING) as log_ctx:
        result = self.gate.should_exit()

    self.assertTrue(result)
    # Verify a WARNING was actually logged (not just a return value)
    self.assertTrue(any("safety breaker" in msg.lower() for msg in log_ctx.output))
```

`self.assertLogs(level=logging.WARNING)` is a context manager that captures all log
messages emitted inside the `with` block. This lets us verify that the code doesn't just
return `True` silently — it must log a warning.

### 11.5 `tests/test_integration.py` (1 test, skipped by default)

```python
@unittest.skipUnless(
    os.environ.get("AMA_INTEGRATION_TEST"),
    "Integration test skipped — set AMA_INTEGRATION_TEST=1 to enable",
)
class TestIntegration(unittest.TestCase):
```

`@unittest.skipUnless(condition, reason)` is a **decorator** — a function that wraps
another function to add behaviour. Here it skips the entire test class unless the
environment variable `AMA_INTEGRATION_TEST` is set. This prevents accidental real API calls
during a regular test run.

The integration test:
1. Creates a temp directory with the full folder structure.
2. Writes a minimal plan file (one task: write "hello world" to a file).
3. Pre-seeds the rate limiter state to allow at most 3 API calls.
4. Sets a 300-second wall-clock timeout using `signal.SIGALRM`.
5. Runs the orchestrator as a subprocess from the temp directory.
6. Asserts that `status.json` was created and all required fields are present.

### 11.6 Running the tests

```bash
# Run all unit tests (integration test is automatically skipped):
.venv/bin/python -m unittest discover tests/

# Verbose output (shows each test name as it runs):
.venv/bin/python -m unittest discover tests/ -v

# Run only the circuit breaker tests:
.venv/bin/python -m unittest tests.test_circuit_breaker

# Run the integration test (makes real API calls!):
AMA_INTEGRATION_TEST=1 .venv/bin/python -m unittest tests.test_integration
```

---

## 12. Directory Structure & Artifacts Reference

```
claude_autonomous_harness/
│
├── ama_orchestrator.py          The main program — run this to start
├── ama_monitor.py               The live dashboard — run in a second terminal
├── requirements.txt             Python package dependencies (rich>=14.0.0)
│
├── ama_safeguards/              The safety system (a Python package)
│   ├── __init__.py              Exports all public names
│   ├── rate_limiter.py          Counts API calls; enforces hourly limit
│   ├── circuit_breaker.py       Detects stuck loops; cuts off calls
│   ├── exit_gate.py             Dual-condition phase completion check
│   └── status_writer.py        Writes status.json; worker registry
│
├── tests/                       Automated test suite
│   ├── __init__.py
│   ├── test_rate_limiter.py     11 unit tests
│   ├── test_circuit_breaker.py  9 unit tests
│   ├── test_exit_gate.py        7 unit tests
│   └── test_integration.py      1 real-API test (skipped by default)
│
├── ama_plans/                   Created automatically at startup
│   ├── initial_plan.md          ← YOU write this
│   ├── architecture_summary.md  Written by Master Claude
│   ├── phase_1_plan.md          Written by Master Claude
│   ├── phase_2_plan.md
│   └── ...
│
├── ama_artifacts/               Created automatically at startup
│   ├── status.json              Live status — read by ama_monitor.py
│   ├── rate_limiter_state.json  RateLimiter persistence
│   ├── circuit_breaker_state.json  CircuitBreaker persistence
│   ├── phase_1_memory.md        Running notes for phase 1
│   ├── worker_1_2_stdout.txt    Raw output from worker 2, loop 1
│   └── ...
│
└── ama_logs/
    └── orchestrator.log         Timestamped log — read by ama_monitor.py
```

---

## 13. Configuration Reference

### Environment Variables

| Variable | Default | Effect |
|---|---|---|
| `AMA_UNATTENDED` | `"0"` | Set to `"1"` to skip all human prompts in the execution phase |
| `AMA_INTEGRATION_TEST` | not set | Set to any value to enable the real-API integration test |

### Constants in `ama_orchestrator.py`

| Constant | Default | Effect |
|---|---|---|
| `X1_MAX_WORKERS` | `3` | Maximum number of worker agents running simultaneously |
| `N_MAX_LOOPS` | `5` | Maximum execution loops per phase before forcing progression |
| `MAX_TURNS` | `"15"` | Maximum tool-use steps a single Claude call can take |

### Constants in `ama_safeguards/rate_limiter.py`

| Constant | Default | Effect |
|---|---|---|
| `HOURLY_CALL_LIMIT` | `10` | Maximum Claude API calls per hour |
| `COOLDOWN_SECONDS` | `3600` | Duration of rate-limit cooldown (1 hour) |

### Constants in `ama_safeguards/circuit_breaker.py`

| Constant | Default | Effect |
|---|---|---|
| `DEFAULT_NO_PROGRESS_THRESHOLD` | `3` | Loops with no progress before OPEN |
| `DEFAULT_SAME_ERROR_THRESHOLD` | `5` | Consecutive identical errors before OPEN |
| `DEFAULT_COOLDOWN_SECONDS` | `1800` | Seconds OPEN waits before HALF_OPEN (30 min) |

### Constants in `ama_monitor.py`

| Constant | Default | Effect |
|---|---|---|
| `REFRESH_HZ` | `2` | Dashboard redraws per second |
| `LOG_TAIL` | `30` | Number of log lines shown in the left panel |

---

## 14. Glossary

| Term | Plain-English Definition |
|---|---|
| **async / await** | Python keywords for writing code that can do multiple things at once without multiple threads |
| **asyncio** | Python's standard library for asynchronous programming |
| **Circuit breaker** | A pattern that "trips" to stop repeated calls when a system is stuck |
| **Cooldown** | A mandatory waiting period after an error, before trying again |
| **Dataclass** | A Python shortcut for creating a class whose main job is holding data fields |
| **Decorator (`@`)** | A function that wraps another function to add extra behavior |
| **Environment variable** | A setting passed to a program from the terminal, outside the code |
| **Event loop** | The machinery inside `asyncio` that schedules and runs async tasks |
| **Exit gate** | The dual-condition check that a phase is genuinely complete |
| **HITL** | Human-In-The-Loop — a step that pauses to ask a human for input |
| **Hour bucket** | A string label for a one-hour time window (e.g. `"2026-03-30T14"`) |
| **ISO 8601** | The international standard format for dates and times (e.g. `2026-03-30T14:32:00Z`) |
| **JSON** | A standard text format for structured data, used for state files and API responses |
| **KPI** | Key Performance Indicator — a specific, measurable goal |
| **Lock** | A mechanism ensuring only one task at a time can access shared data |
| **Module-level variable** | A variable defined at the top of a file, shared by all code that imports the file |
| **Package** | A folder containing an `__init__.py` file, treated by Python as a module |
| **Rate limit** | An API restriction on how many calls can be made per time period |
| **Rate limiter** | Software that counts and controls API call frequency |
| **Regular expression (regex)** | A mini-language for describing text patterns (e.g. `"password\s*:"` matches "password :") |
| **Semaphore** | A counter that limits how many tasks can run concurrently |
| **State persistence** | Saving program state to disk so it survives crashes and restarts |
| **stderr** | The "error output" stream of a program (separate from normal output) |
| **stdout** | The normal output stream of a program |
| **Subprocess** | An external program launched and controlled by your Python script |
| **Unix timestamp** | Number of seconds since January 1, 1970 — Python's `time.time()` format |
| **UTC** | Coordinated Universal Time — the standard timezone for computing |
| **Worker agent** | An independent instance of Claude that executes one specific task |
