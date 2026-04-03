# Architecture Summary: AMA Orchestrator Safeguards

## Overview

This project adds production-grade safeguards to the existing `main.py` — a
multi-agent Claude orchestration system that dispatches parallel worker sub-processes to execute
plans. The goal is to prevent infinite loops, runaway API spending, and premature exits during
unattended long-running runs, using only standard Python + `rich` (no external services).

---

## System Components

```
main.py          — existing orchestration entry point (modified in Phase 2)
src/safeguards/
  __init__.py
  rate_limiter.py            — Phase 1: hourly call tracking + rate-limit detection
  circuit_breaker.py         — Phase 1: stuck-loop detection + 3-state machine
  exit_gate.py               — Phase 1: dual-condition phase completion gate
  status_writer.py           — Phase 2: writes .artifacts/status.json each loop
ama_monitor.py               — Phase 3: standalone Rich terminal dashboard
tests/
  test_rate_limiter.py
  test_circuit_breaker.py
  test_exit_gate.py
  test_integration.py
requirements.txt             — must be created/updated before Phase 3 (adds `rich>=14.0.0`)
.artifacts/
  status.json                — live status + ExitGate state (written by orchestrator, read by monitor)
  rate_limiter_state.json    — persisted rate limiter state
  circuit_breaker_state.json — persisted circuit breaker state
  worker_{loop_num}_{id}_stdout.txt  — persisted raw worker subprocess output; loop_num prevents overwrites
```

---

## Data Flow

```
User
 └─ python main.py "task"
     │
     ├─ plan_refinement_phase()
     │   ├─ run_orchestrator() ──→ [RateLimiter check] ──→ subprocess(claude -p ...)
     │   └─ run_worker_agent() ──→ [RateLimiter check] ──→ asyncio.subprocess(claude -p ...)
     │
     └─ execution_phase() [loop]
         ├─ run_orchestrator() ──→ [RateLimiter check] ──→ subprocess(claude -p ...)
         ├─ run_worker_agent()×N ──→ [RateLimiter check] ──→ asyncio.subprocess(claude -p ...)×N
         ├─ [CircuitBreaker.record_loop_result()] ──→ OPEN? → wait/raise
         ├─ [ExitGate.record_worker_outputs() + record_kpi_review()] ──→ exit? → break
         └─ [StatusWriter.write_status()] ──→ .artifacts/status.json

python ama_monitor.py  (separate terminal)
 └─ asyncio event loop
     ├─ tail_log() task ──→ polls .logs/*.log → updates left panel
     ├─ poll_status() task ──→ polls .artifacts/status.json → updates right panels
     └─ with Live(layout, screen=True, refresh_per_second=2): [Rich internal thread redraws]
```

---

## Key Design Decisions

### 1. Safeguards as Independent Modules (not a God class)
Each safeguard has a single responsibility and can be tested, imported, and reasoned about
independently. The orchestrator passes them as arguments rather than importing globals.

### 2. State Persistence via JSON
All mutable safeguard state is written to `.artifacts/*.json` after every mutation.
This includes `ExitGate` state (heuristic score + KPI confirmation), which is written into
`status.json` by `StatusWriter`. A crashed orchestrator can resume with the correct counters,
circuit-breaker state, and exit-gate progress on restart.

### 3. Sync/Async Boundary Handling
`run_orchestrator()` is synchronous (blocks the event loop) — this is a pre-existing design
constraint. The rate-limiter check there is also synchronous (no regression). Worker agents
are async; their rate-limiter check uses `await asyncio.sleep()` to stay non-blocking.

### 4. Rich Dashboard: `with Live(...)` + asyncio tasks
`rich.live.Live` does NOT support `async with`. The correct pattern is:
- Open `Live` with synchronous `with Live(..., screen=True)` as the outermost context
- Run update coroutines as `asyncio.create_task()`s inside the `with` block
- Tasks call `layout["panel"].update(new_content)` directly
- `Live`'s internal `_RefreshThread` handles screen redraws on its own timer

### 5. Dual-Condition Exit Gate
A single `kpis_met: true` JSON field from the orchestrator is insufficient to exit a phase —
it can be an LLM hallucination or refer to a sub-task. The heuristic score (parsed from worker
outputs) provides independent corroboration. The 5-signal safety breaker prevents the system
from getting permanently stuck in "almost done" limbo.

### 6. `UNATTENDED_MODE` via Environment Variable
`AMA_UNATTENDED=1` enables fully autonomous operation: rate-limit waits, circuit-breaker
cooldowns, and skipped HITL prompts. Default is `0` (interactive), preserving existing behavior.

---

## Dependency Graph (Phase Order)

```
Phase 1 (rate_limiter, circuit_breaker, exit_gate)
    │
    └──→ Phase 2 (integrate into main.py + status_writer)
              │
              └──→ Phase 3 (ama_monitor.py — can start once status.json schema is fixed)
                        │
                        └──→ Phase 4 (tests — can begin against Phase 1 immediately)
```

Phase 3 and Phase 4 (unit tests only) can overlap with Phase 2.

---

## External Dependencies

| Package | Version | Purpose | Already installed? |
|---------|---------|---------|-------------------|
| `rich`  | >=14.0.0 | Terminal dashboard | No — must install |

All other code uses Python stdlib (`asyncio`, `subprocess`, `json`, `pathlib`, `time`, `os`,
`dataclasses`, `threading`).

**Install command:** `.venv/bin/pip install "rich>=14.0.0"`

### `requirements.txt` (must be created/updated in Phase 2 before Phase 3 starts)

An explicit step must create or update `requirements.txt` (or `pyproject.toml` if one exists)
with the `rich` dependency **before** Phase 3 implementation begins:

```
# requirements.txt
rich>=14.0.0
```

Worker: verify the file exists at project root; create it if absent. Add it to source control.

---

## Risk Register

| Risk | Mitigation |
|------|-----------|
| `run_orchestrator` blocks event loop during rate-limit wait | Already blocking; use sync `time.sleep(seconds_until_reset)` with prominent log/console output so human knows why progress stalled |
| `rich.Live` alternate screen corrupts terminal on crash | `with Live(...)` guarantees `__exit__` runs; catches `KeyboardInterrupt` |
| Circuit breaker opens on legitimate slow progress | `kpi_advancement` flag prevents false positives when KPIs advance even with few file changes |
| False-positive rate-limit detection (narrative text) | Layer 1 (JSON structural) is primary signal; text pattern only fires on `stderr` or last 30 lines of stdout, not narrative body |
| State files corrupted between runs | Wrap JSON loads in try/except; re-initialize on corruption |
