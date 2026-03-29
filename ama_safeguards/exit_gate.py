"""
ExitGate: Dual-condition phase completion gate.

A phase exits ONLY when BOTH conditions are met:
  1. Heuristic score >= 2  (completion keywords parsed from worker outputs)
  2. KPI review explicitly confirms kpis_met == True

Safety breaker (KPI-1.10):
  After 5 consecutive loops that produce completion signals, force exit
  regardless of the KPI confirmation state — prevents infinite "almost done" loops.

KPIs covered: 1.7, 1.8, 1.9, 1.10
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Completion keywords (longer multi-word phrases listed first so the regex
# alternation prefers them before shorter single words)
# ---------------------------------------------------------------------------
_COMPLETION_KEYWORDS: list[str] = [
    "task complete",
    "all tests pass",
    "kpis met",
    "finished",
    "done",
]

# Heading words that trigger "anywhere-in-line" scanning mode for the section below them
_SECTION_TRIGGER_WORDS: frozenset[str] = frozenset({"summary", "result", "status"})

_SCORE_CAP: int = 5
_SAFETY_BREAKER_THRESHOLD: int = 5

# Regex: one of the keywords at the *start* of a line (after optional whitespace),
# bounded by \b so partial words (e.g. "undone") are excluded.
_KEYWORD_LINE_RE = re.compile(
    r"^\s*(?:" + "|".join(re.escape(k) for k in _COMPLETION_KEYWORDS) + r")\b",
    re.IGNORECASE | re.MULTILINE,
)

# Markdown heading detector — captures heading text in group 1
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# State dataclass (serialisable by StatusWriter)
# ---------------------------------------------------------------------------

@dataclass
class ExitGateState:
    consecutive_completion_signals: int = 0
    kpis_met_confirmed: bool = False
    heuristic_score: int = 0


# ---------------------------------------------------------------------------
# ExitGate
# ---------------------------------------------------------------------------

class ExitGate:
    """
    Enforces a dual-condition exit rule for phase completion.

    Usage::

        gate = ExitGate()
        gate.record_worker_outputs(["Task complete. All tests pass."])
        gate.record_kpi_review(kpis_met=True)
        if gate.should_exit():
            break  # leave the orchestration loop
    """

    def __init__(self, safety_breaker_threshold: int = _SAFETY_BREAKER_THRESHOLD):
        self._threshold = safety_breaker_threshold
        self._state = ExitGateState()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def should_exit(self) -> bool:
        """
        Evaluate whether the current phase is complete.

        Returns True when:
          - heuristic_score >= 2  AND  kpis_met_confirmed is True  (normal path), OR
          - consecutive_completion_signals >= safety_breaker_threshold  (force-exit path).

        KPI-1.7: returns False when heuristic_score >= 2 but kpis_met_confirmed is False.
        KPI-1.8: returns False when kpis_met_confirmed is True but heuristic_score < 2.
        KPI-1.9: returns True when both conditions are met.
        KPI-1.10: returns True (with WARNING) after 5 consecutive completion signals.
        """
        # Safety breaker check (KPI-1.10)
        if self._state.consecutive_completion_signals >= self._threshold:
            logger.warning(
                "ExitGate safety breaker: %d consecutive completion signals >= %d — forcing exit",
                self._state.consecutive_completion_signals,
                self._threshold,
            )
            return True

        # Normal dual-condition check (KPI-1.9)
        if self._state.heuristic_score >= 2 and self._state.kpis_met_confirmed:
            logger.info(
                "ExitGate: dual-condition met (heuristic_score=%d >= 2, kpis_met=True) — exiting phase",
                self._state.heuristic_score,
            )
            return True

        # Log why we are NOT exiting (aids debugging)
        logger.debug(
            "ExitGate: not exiting — heuristic_score=%d/2, kpis_met_confirmed=%s, "
            "consecutive_signals=%d/%d",
            self._state.heuristic_score,
            self._state.kpis_met_confirmed,
            self._state.consecutive_completion_signals,
            self._threshold,
        )
        return False

    def record_worker_outputs(self, outputs: list[str]) -> None:
        """
        Parse worker output strings for completion keywords and update heuristic_score.

        Scoring rules:
          - Each distinct completion keyword found across ALL provided outputs adds +1.
          - heuristic_score is capped at _SCORE_CAP (5).
          - If any keywords are found, consecutive_completion_signals is incremented.

        Args:
            outputs: List of raw worker output strings from one orchestration loop.
        """
        found_keywords: set[str] = set()
        for output in outputs:
            found_keywords.update(_find_completion_keywords(output))

        if found_keywords:
            hits = len(found_keywords)
            old_score = self._state.heuristic_score
            self._state.heuristic_score = min(_SCORE_CAP, self._state.heuristic_score + hits)
            self._state.consecutive_completion_signals += 1
            logger.debug(
                "ExitGate: found keyword(s) %s in worker outputs; "
                "heuristic_score %d → %d; consecutive_signals=%d",
                found_keywords,
                old_score,
                self._state.heuristic_score,
                self._state.consecutive_completion_signals,
            )
        else:
            logger.debug(
                "ExitGate: no completion keywords found in %d worker output(s); "
                "heuristic_score=%d unchanged",
                len(outputs),
                self._state.heuristic_score,
            )

    def record_kpi_review(self, kpis_met: bool) -> None:
        """
        Record the result of an orchestrator KPI review.

        Once set to True, kpis_met_confirmed remains True until reset() is called.

        Args:
            kpis_met: Whether the orchestrator's KPI review returned kpis_met == True.
        """
        if kpis_met and not self._state.kpis_met_confirmed:
            self._state.kpis_met_confirmed = True
            logger.info("ExitGate: KPI review confirmed — kpis_met_confirmed = True")
        else:
            logger.debug(
                "ExitGate: KPI review kpis_met=%s (kpis_met_confirmed=%s, no change)",
                kpis_met,
                self._state.kpis_met_confirmed,
            )

    def reset(self) -> None:
        """
        Reset all gate state back to initial values.

        Call this when starting a new phase so counts from the previous phase
        do not bleed into the next.
        """
        logger.info(
            "ExitGate: resetting state "
            "(was: heuristic_score=%d, kpis_met=%s, consecutive_signals=%d)",
            self._state.heuristic_score,
            self._state.kpis_met_confirmed,
            self._state.consecutive_completion_signals,
        )
        self._state = ExitGateState()

    # ------------------------------------------------------------------
    # State accessors (for StatusWriter integration and testing)
    # ------------------------------------------------------------------

    def get_state(self) -> ExitGateState:
        """Return a snapshot of the current gate state (for serialisation)."""
        return ExitGateState(
            consecutive_completion_signals=self._state.consecutive_completion_signals,
            kpis_met_confirmed=self._state.kpis_met_confirmed,
            heuristic_score=self._state.heuristic_score,
        )

    def restore_state(self, state: ExitGateState) -> None:
        """
        Restore a previously serialised ExitGateState (crash-recovery path).

        Args:
            state: An ExitGateState dataclass, typically deserialised from status.json.
        """
        self._state = state
        logger.debug(
            "ExitGate: state restored "
            "(heuristic_score=%d, kpis_met=%s, consecutive_signals=%d)",
            self._state.heuristic_score,
            self._state.kpis_met_confirmed,
            self._state.consecutive_completion_signals,
        )


# ---------------------------------------------------------------------------
# Heuristic scoring helpers (module-private)
# ---------------------------------------------------------------------------

def _find_completion_keywords(text: str) -> set[str]:
    """
    Find distinct completion keywords present in *text*.

    A keyword is counted when it appears in one of two contexts:

    1. **Line-anchored**: at the very start of a line (after optional whitespace),
       followed by a word boundary.  Prevents mid-sentence false positives such as
       "the migration was not finished because…".

    2. **Section-scanned**: anywhere on a line that falls inside a markdown section
       whose heading contains "summary", "result", or "status" (case-insensitive).
       This captures structured output like::

           ## Summary
           Task complete. All tests pass.

    Returns:
        A set of canonical (lowercase) keyword strings.
    """
    found: set[str] = set()

    # --- Strategy 1: keyword at start of line ----------------------------
    for match in _KEYWORD_LINE_RE.finditer(text):
        raw = match.group(0).strip().lower()
        # Map matched text back to its canonical keyword
        for kw in _COMPLETION_KEYWORDS:
            if raw.startswith(kw):
                found.add(kw)
                break

    # --- Strategy 2: keyword anywhere inside a relevant heading section --
    in_target_section = False
    for line in text.splitlines():
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            heading_text = heading_match.group(1).lower()
            in_target_section = any(
                trigger in heading_text for trigger in _SECTION_TRIGGER_WORDS
            )
            continue

        if in_target_section:
            line_lower = line.lower()
            for kw in _COMPLETION_KEYWORDS:
                if kw in line_lower:
                    found.add(kw)

    return found
