"""The engine: run all detectors over a session's recent events, aggregate.

Highest-severity verdict wins (BLOCK > NUDGE > ALLOW). Two global behaviors
live here:

  * mode "off"     -> always ALLOW (per-project opt-out / kill switch).
  * mode != enforce -> BLOCK is downgraded to a NUDGE tagged "[observe]".
    This is the safe rollout path: run in observe mode first, watch what it
    *would* have blocked in your real workflow, tune thresholds, then flip to
    "enforce" only once you trust it.

Fail-open is enforced at every layer: a broken detector is skipped, an
unreadable store yields no events, and any unexpected error returns ALLOW.
"""
from __future__ import annotations

from typing import Optional

from .events import ToolEvent
from .state import read_recent
from ..detectors.base import ALLOW, BLOCK, NUDGE, Verdict
from ..detectors.repetition import RepetitionDetector
from ..detectors.error_streak import ErrorStreakDetector

# Registry. Add a detector here to enable it.
DETECTORS = [
    RepetitionDetector(),
    ErrorStreakDetector(),
]


def _allow() -> Verdict:
    return Verdict(ALLOW, "engine", "")


def evaluate(
    session_id: str,
    config: dict,
    candidate: Optional[ToolEvent] = None,
) -> Verdict:
    try:
        if config.get("mode") == "off":
            return _allow()

        window = int(config.get("window", 12))
        events = read_recent(session_id, window)

        best = _allow()
        for det in DETECTORS:
            try:
                v = det.evaluate(events, candidate, config)
            except Exception:
                continue  # a broken detector never blocks a call
            if v and v.rank > best.rank:
                best = v

        if config.get("mode") != "enforce" and best.action == BLOCK:
            # observe mode: surface what would have happened, but never block.
            return Verdict(NUDGE, best.detector, "[observe] WOULD BLOCK — " + best.reason)
        return best
    except Exception:
        return _allow()
