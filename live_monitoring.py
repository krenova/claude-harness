"""
ama_monitor.py — Standalone Rich terminal dashboard for the AMA Orchestrator.

Polls .artifacts/status.json and tails .logs/orchestrator.log, displaying
everything in a 3-panel full-screen layout. Run as a separate process alongside
the orchestrator — no tmux required.

Usage:
    .venv/bin/python ama_monitor.py
"""

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure src.safeguards is importable when running from the project root
sys.path.insert(0, str(Path(__file__).parent))

from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from src.safeguards.rate_limiter import HOURLY_CALL_LIMIT

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

STATUS_FILE = Path(".artifacts/status.json")
LOG_FILE    = Path(".logs/orchestrator.log")
REFRESH_HZ  = 2    # Live redraws per second
LOG_TAIL    = 30   # Number of log lines displayed in the left panel


# ---------------------------------------------------------------------------
# Layout factory
# ---------------------------------------------------------------------------

def make_layout() -> Layout:
    """Build the 3-panel layout tree."""
    layout = Layout(name="root")
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body", ratio=1),
    )
    layout["body"].split_row(
        Layout(name="left", ratio=3),   # log stream — wider
        Layout(name="right", ratio=2),  # status + workers
    )
    layout["right"].split_column(
        Layout(name="status", ratio=2),
        Layout(name="workers", ratio=1),
    )
    return layout


# ---------------------------------------------------------------------------
# Panel builders
# ---------------------------------------------------------------------------

def build_header(status: dict) -> Panel:
    phase      = status.get("phase", "—")
    loop_count = status.get("loop_count", "—")
    ts         = datetime.now().strftime("%H:%M:%S")

    text = Text(justify="center")
    text.append("AMA Orchestrator Monitor", style="bold white")
    text.append("  │  Phase: ", style="dim")
    text.append(str(phase), style="bold cyan")
    text.append("  │  Loop: ", style="dim")
    text.append(str(loop_count), style="bold cyan")
    text.append("  │  ", style="dim")
    text.append(ts, style="dim white")

    return Panel(text, border_style="bold blue")


def _log_line_style(line: str) -> str:
    """Return a Rich style string for a single log line."""
    if "[ERROR]" in line or "❌" in line:
        return "red"
    if "[WARNING]" in line or "🚦" in line or "⚡" in line:
        return "yellow"
    if "✅" in line or "🎉" in line:
        return "green"
    return "white"


def build_log_panel(lines: list[str]) -> Panel:
    text = Text()
    for line in lines:
        text.append(line + "\n", style=_log_line_style(line))
    return Panel(text, title="Live Log Stream", border_style="blue")


def build_status_panel(status: dict) -> Panel:
    calls          = status.get("api_calls_this_hour", 0)
    cb_state       = status.get("circuit_breaker_state", "—")
    heuristic      = status.get("exit_gate_heuristic_score", 0)
    kpis_met       = status.get("exit_gate_kpis_met", False)
    cooldown_until = status.get("rate_limit_cooldown_until")
    updated_at     = status.get("updated_at", "—")

    text = Text()

    # --- API calls with ASCII fill bar ---
    limit      = HOURLY_CALL_LIMIT
    bar_fill   = int(min(calls, limit) / limit * 20) if limit else 0
    bar        = "▓" * bar_fill + "░" * (20 - bar_fill)
    call_style = "bold red" if calls >= limit else "white"
    text.append("API calls:   ", style="dim")
    text.append(f"{calls} / {limit}", style=call_style)
    text.append(f"  [{bar}]\n")

    # --- Circuit breaker ---
    text.append("Circuit:     ", style="dim")
    if cb_state == "CLOSED":
        text.append("CLOSED ✅\n", style="green")
    elif cb_state == "OPEN":
        text.append("OPEN ⚡\n", style="bold red")
    elif cb_state == "HALF_OPEN":
        text.append("HALF_OPEN ⚠️\n", style="yellow")
    else:
        text.append(f"{cb_state}\n", style="dim")

    # --- Exit gate ---
    kpi_sym   = "✅" if kpis_met else "✗"
    kpi_style = "green" if kpis_met else "red"
    text.append("Exit gate:   ", style="dim")
    text.append(f"heuristic={heuristic}/2  KPI=")
    text.append(kpi_sym + "\n", style=kpi_style)

    # --- Rate limit ---
    text.append("Rate limit:  ", style="dim")
    if cooldown_until is not None:
        remaining = max(0.0, cooldown_until - time.time())
        text.append(f"cooldown {remaining:.0f}s\n", style="yellow")
    else:
        text.append("OK\n", style="green")

    # --- Last updated ---
    display_ts = (
        updated_at.replace("T", " ").replace("Z", " UTC")
        if updated_at and updated_at != "—"
        else "—"
    )
    text.append("Updated:     ", style="dim")
    text.append(display_ts + "\n", style="dim white")

    return Panel(text, title="Status Overview", border_style="blue")


def build_workers_panel(status: dict) -> Panel:
    active: list[int] = status.get("active_workers", [])
    text = Text()
    if active:
        for wid in sorted(active):
            text.append(f"Worker {wid}", style="bold")
            text.append("  running\n", style="green")
    else:
        text.append("No active workers\n", style="dim")
    return Panel(text, title="Active Workers", border_style="blue")


# ---------------------------------------------------------------------------
# Async updaters
# ---------------------------------------------------------------------------

async def tail_log(layout: Layout) -> None:
    """Poll the log file for changes; refresh the left panel with the last LOG_TAIL lines."""
    last_size: int = -1
    lines: list[str] = []

    while True:
        try:
            if LOG_FILE.exists():
                size = LOG_FILE.stat().st_size
                if size != last_size:
                    last_size = size
                    raw = LOG_FILE.read_text(errors="replace")
                    lines = raw.splitlines()[-LOG_TAIL:]
            else:
                lines = ["[Waiting for log file…]"]
        except OSError:
            lines = ["[Log file unreadable]"]

        layout["left"].update(build_log_panel(lines))
        await asyncio.sleep(0.5)


async def poll_status(layout: Layout) -> None:
    """Poll status.json every 1s; refresh header, status, and workers panels."""
    while True:
        status: dict = {}
        try:
            if STATUS_FILE.exists():
                raw = STATUS_FILE.read_text(errors="replace")
                status = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            pass  # keep previous status; panels retain last good state

        layout["header"].update(build_header(status))
        layout["status"].update(build_status_panel(status))
        layout["workers"].update(build_workers_panel(status))
        await asyncio.sleep(1.0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    layout = make_layout()

    # Seed all panels with placeholder content (KPI-3.7: graceful startup)
    layout["header"].update(build_header({}))
    layout["left"].update(build_log_panel(["[Waiting for log file…]"]))
    layout["status"].update(build_status_panel({}))
    layout["workers"].update(build_workers_panel({}))

    # Live is NOT async-compatible — use the sync context manager.
    # The internal _RefreshThread handles screen redraws; no manual refresh() needed.
    with Live(layout, screen=True, refresh_per_second=REFRESH_HZ):
        await asyncio.gather(
            tail_log(layout),
            poll_status(layout),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass  # Live.__exit__ already restored the terminal
