"""The engine: run all ENABLED detectors over a session's recent events.

Highest-severity verdict wins (BLOCK > NUDGE > ALLOW). Three global behaviors
live here:

  * mode "off"      -> always ALLOW (per-project opt-out / kill switch).
  * enable/disable  -> a detector whose config has enabled:false is skipped
    HERE, centrally, so a detector author can't forget the check and have it
    silently run anyway.
  * mode != enforce -> a BLOCK is returned as a NUDGE with would_block=True.
    The verdict carries the downgrade as DATA (would_block), not as a magic
    string in `reason`, so the transcript-tail supervisor and telemetry can
    count would-blocks without parsing prose. This is the safe rollout path:
    run observe first, watch would_block, tune, then flip to enforce.

Fail-open is enforced here: a broken detector is skipped, an unreadable store
yields no events, and any unexpected error returns ALLOW.
"""
from __future__ import annotations

from typing import Optional

from .events import ToolEvent
from .state import read_recent
from ..detectors.base import ALLOW, BLOCK, NUDGE, Verdict
from ..detectors.repetition import RepetitionDetector
from ..detectors.error_streak import ErrorStreakDetector
from ..detectors.oscillation import OscillationDetector

# Registry. Add a detector here to enable it. Order matters only for ties:
# on equal severity the earlier detector's verdict is kept (see evaluate()),
# so list them most-precise first.
DETECTORS = [
    RepetitionDetector(),
    OscillationDetector(),
    ErrorStreakDetector(),
]


def _allow() -> Verdict:
    return Verdict(ALLOW, "engine", "")


def _enabled(config: dict, name: str) -> bool:
    dc = (config.get("detectors", {}) or {}).get(name, {}) or {}
    return bool(dc.get("enabled", True))


def evaluate(
    session_id: str,
    config: dict,
    candidate: Optional[ToolEvent] = None,
) -> Verdict:
    try:
        mode = config.get("mode")
        if mode == "off":
            return _allow()

        try:
            window = int(config.get("window", 12))
        except (TypeError, ValueError):
            window = 12
        if window < 1:
            window = 1

        events = read_recent(session_id, window)

        best = _allow()
        for det in DETECTORS:
            if not _enabled(config, det.name):  # central enable gate
                continue
            try:
                v = det.evaluate(events, candidate, config)
            except Exception:
                continue  # a broken detector never blocks a call
            if v and v.rank > best.rank:
                best = v

        if mode != "enforce" and best.action == BLOCK:
            # observe mode: do not block; carry the downgrade as structured data.
            return Verdict(NUDGE, best.detector, best.reason, would_block=True)
        return best
    except Exception:
        return _allow()
