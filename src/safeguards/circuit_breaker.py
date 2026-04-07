"""
CircuitBreaker: Detects when the orchestration loop is stuck and opens a circuit
to prevent burning program calls on a spinning/no-progress loop.

States:
  CLOSED    → normal operation
  OPEN      → stuck detected, calls blocked
  HALF_OPEN → cooldown elapsed, trial call allowed
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_CLOSED = "CLOSED"
STATE_OPEN = "OPEN"
STATE_HALF_OPEN = "HALF_OPEN"

DEFAULT_STATE_PATH = Path(".artifacts/circuit_breaker_state.json")
DEFAULT_NO_PROGRESS_THRESHOLD = 3
DEFAULT_SAME_ERROR_THRESHOLD = 5
DEFAULT_COOLDOWN_SECONDS = 1800


class CircuitBreaker:
    """
    Monitors loop progress and trips open when no progress is detected.

    Transition rules:
      - consecutive_no_progress >= 3  → OPEN
      - consecutive_same_error >= 5   → OPEN
      - OPEN + cooldown elapsed        → HALF_OPEN  (via check_cooldown())
      - HALF_OPEN + progress detected  → CLOSED     (via record_loop_result())
      - HALF_OPEN + no progress        → OPEN       (reset cooldown timer)
    """

    def __init__(
        self,
        state_path: Path = DEFAULT_STATE_PATH,
        no_progress_threshold: int = DEFAULT_NO_PROGRESS_THRESHOLD,
        same_error_threshold: int = DEFAULT_SAME_ERROR_THRESHOLD,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
        time_fn=None,
    ):
        self._state_path = Path(state_path)
        self._no_progress_threshold = no_progress_threshold
        self._same_error_threshold = same_error_threshold
        self._cooldown_seconds = cooldown_seconds
        # Injectable clock — defaults to time.time; override in tests for determinism.
        self._time_fn = time_fn if time_fn is not None else time.time
        self._load_state()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def is_open(self) -> bool:
        """Return True if the circuit is OPEN (calls should be blocked)."""
        return self._state == STATE_OPEN

    def get_state(self) -> str:
        """Return the current state string: 'CLOSED', 'OPEN', or 'HALF_OPEN'."""
        return self._state

    @property
    def cooldown_seconds(self) -> int:
        """Duration (in seconds) to wait before transitioning OPEN → HALF_OPEN."""
        return self._cooldown_seconds

    def check_cooldown(self) -> bool:
        """
        If the circuit is OPEN and the cooldown period has elapsed,
        transition to HALF_OPEN and return True.
        Returns False if cooldown has not elapsed or circuit is not OPEN.
        """
        if self._state != STATE_OPEN:
            return False
        if self._opened_at is None:
            return False

        elapsed = self._time_fn() - self._opened_at
        if elapsed >= self._cooldown_seconds:
            logger.info(
                "CircuitBreaker: cooldown elapsed (%.1fs >= %ds), transitioning OPEN → HALF_OPEN",
                elapsed,
                self._cooldown_seconds,
            )
            self._state = STATE_HALF_OPEN
            self._save_state()
            return True
        return False

    def record_loop_result(
        self,
        files_changed: int,
        worker_artifacts_produced: int,
        kpi_advancement: bool,
        error_signature: str | None,
    ) -> None:
        """
        Record the outcome of one orchestration loop iteration.

        Progress is detected when any of:
          - files_changed > 0
          - worker_artifacts_produced > 0
          - kpi_advancement is True

        Handles HALF_OPEN → CLOSED or HALF_OPEN → OPEN transitions internally.
        """
        progress = files_changed > 0 or worker_artifacts_produced > 0 or kpi_advancement

        if self._state == STATE_HALF_OPEN:
            if progress:
                logger.info(
                    "CircuitBreaker: progress detected in HALF_OPEN, transitioning → CLOSED"
                )
                self._reset_counters()
                self._state = STATE_CLOSED
                self._opened_at = None
            else:
                logger.warning(
                    "CircuitBreaker: no progress in HALF_OPEN trial, transitioning → OPEN (reset cooldown)"
                )
                self._state = STATE_OPEN
                self._opened_at = self._time_fn()
            self._save_state()
            return

        if progress:
            # Progress resets the no-progress counter ONLY.
            # The same-error streak accumulates independently — a persistent error
            # signature is a stuck-loop signal even when files are being changed.
            if self._consecutive_no_progress > 0:
                logger.debug("CircuitBreaker: progress detected, resetting no-progress counter")
            self._consecutive_no_progress = 0
        else:
            # No progress — increment no-progress counter
            self._consecutive_no_progress += 1
            logger.debug(
                "CircuitBreaker: no progress (consecutive=%d/%d)",
                self._consecutive_no_progress,
                self._no_progress_threshold,
            )

        # Track same-error streak
        if error_signature is not None:
            if error_signature == self._last_error_signature:
                self._consecutive_same_error += 1
                logger.debug(
                    "CircuitBreaker: same error streak=%d/%d sig=%r",
                    self._consecutive_same_error,
                    self._same_error_threshold,
                    error_signature,
                )
            else:
                self._last_error_signature = error_signature
                self._consecutive_same_error = 1
        else:
            # No error this iteration — reset error streak
            self._consecutive_same_error = 0
            self._last_error_signature = None

        # Check trip conditions
        if self._consecutive_no_progress >= self._no_progress_threshold:
            logger.warning(
                "CircuitBreaker: no-progress threshold reached (%d), transitioning CLOSED → OPEN",
                self._consecutive_no_progress,
            )
            self._state = STATE_OPEN
            self._opened_at = self._time_fn()
        elif self._consecutive_same_error >= self._same_error_threshold:
            logger.warning(
                "CircuitBreaker: same-error threshold reached (%d), transitioning CLOSED → OPEN",
                self._consecutive_same_error,
            )
            self._state = STATE_OPEN
            self._opened_at = self._time_fn()

        self._save_state()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _reset_counters(self) -> None:
        self._consecutive_no_progress = 0
        self._consecutive_same_error = 0
        self._last_error_signature = None

    def _load_state(self) -> None:
        """Load persisted state from JSON, or initialise defaults."""
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text())
                self._state = data.get("state", STATE_CLOSED)
                self._consecutive_no_progress = int(data.get("consecutive_no_progress", 0))
                self._consecutive_same_error = int(data.get("consecutive_same_error", 0))
                self._last_error_signature = data.get("last_error_signature")
                self._opened_at = data.get("opened_at")
                # Override cooldown_seconds only if not already set by constructor default
                if "cooldown_seconds" in data and self._cooldown_seconds == DEFAULT_COOLDOWN_SECONDS:
                    self._cooldown_seconds = int(data["cooldown_seconds"])
                logger.debug("CircuitBreaker: loaded state %r from %s", self._state, self._state_path)
                return
            except Exception as exc:
                logger.warning("CircuitBreaker: failed to load state from %s: %s", self._state_path, exc)

        # Defaults
        self._state = STATE_CLOSED
        self._consecutive_no_progress = 0
        self._consecutive_same_error = 0
        self._last_error_signature = None
        self._opened_at = None

    def _save_state(self) -> None:
        """Persist current state to JSON."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "state": self._state,
            "consecutive_no_progress": self._consecutive_no_progress,
            "consecutive_same_error": self._consecutive_same_error,
            "last_error_signature": self._last_error_signature,
            "opened_at": self._opened_at,
            "cooldown_seconds": self._cooldown_seconds,
        }
        self._state_path.write_text(json.dumps(data, indent=2))
        logger.debug("CircuitBreaker: saved state %r to %s", self._state, self._state_path)
