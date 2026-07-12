"""The engine: run all ENABLED detectors over a session's recent events.

Highest-severity verdict wins (BLOCK > NUDGE > ALLOW). Four central behaviors
live here:

  * mode "off"      -> always ALLOW (per-project opt-out / kill switch).
  * enable/disable  -> a detector whose config has enabled:false is skipped
    HERE, centrally, so a detector author can't forget the check and have it
    silently run anyway.
  * detector mode   -> a detector can override the global mode, so narrow
    high-signal rails can enforce while broad rails stay observe.
  * mode != enforce -> a BLOCK is returned as a NUDGE with would_block=True.
    The verdict carries the downgrade as DATA (would_block), not as a magic
    string in `reason`, so the transcript-tail supervisor and telemetry can
    count would-blocks without parsing prose. This is the safe rollout path:
    run observe first, watch would_block, tune, then enforce globally or for
    one detector at a time.

Fail-open is enforced here: a broken detector is skipped, an unreadable store
yields no events, and any unexpected error returns ALLOW.
"""
from __future__ import annotations

from typing import Optional

from .events import ToolEvent
from .budget import read_state as read_budget_state
from .state import read_recent
from ..detectors.base import ALLOW, BLOCK, NUDGE, Verdict
from ..detectors.repetition import RepetitionDetector
from ..detectors.error_streak import ErrorStreakDetector
from ..detectors.oscillation import OscillationDetector
from ..detectors.leverage_fallback import LeverageFallbackDetector
from ..detectors.workflow_wrapper import WorkflowWrapperDetector
from ..detectors.read_discipline import ReadDisciplineDetector
from ..detectors.python_command import PythonCommandDetector

# Registry. Add a detector here to enable it. Order matters only for ties:
# on equal severity the earlier detector's verdict is kept (see evaluate()),
# so list them most-precise first.
DETECTORS = [
    WorkflowWrapperDetector(),
    LeverageFallbackDetector(),
    PythonCommandDetector(),
    ReadDisciplineDetector(),
    RepetitionDetector(),
    OscillationDetector(),
    ErrorStreakDetector(),
]

# Resource/workflow guidance may advise but cannot become a mechanical denial.
_ADVISORY_ONLY = {"workflow_wrapper", "read_discipline", "python_command"}


def _allow() -> Verdict:
    return Verdict(ALLOW, "engine", "")


def _enabled(config: dict, name: str) -> bool:
    dc = (config.get("detectors", {}) or {}).get(name, {}) or {}
    return bool(dc.get("enabled", True))


def _mode(config: dict, name: Optional[str] = None) -> str:
    if name is not None:
        dc = (config.get("detectors", {}) or {}).get(name, {}) or {}
        dm = dc.get("mode")
        if dm in {"off", "observe", "enforce"}:
            return dm
    gm = config.get("mode")
    if gm in {"off", "observe", "enforce"}:
        return gm
    return "observe"


def _recovery_packet(
    session_id: str,
    verdict: Verdict,
    candidate: Optional[ToolEvent],
    events: list[ToolEvent],
) -> dict:
    """Build the minimum recovery artifact; optional context is best-effort."""
    signature = candidate.arg_hash if candidate is not None else "unknown"
    safe_session = "".join(
        char if char.isalnum() or char in "-_" else "_" for char in str(session_id)
    ) or "default"
    try:
        if verdict.detector == "error_streak":
            streak: list[str] = []
            for event in reversed(events):
                if event.status != "error":
                    break
                streak.append(event.arg_hash)
            signature = "error_streak:" + ",".join(reversed(streak))
        elif verdict.detector == "oscillation":
            signature = "oscillation:" + ",".join(
                event.arg_hash for event in events[-4:]
            )
    except Exception:
        pass
    packet = {
        "detector": verdict.detector,
        "signature": signature,
        "next_action": "Run one materially different, read-only diagnostic.",
        "reset_command": f"hamingja recover {safe_session} reset",
        "handoff_command": f"hamingja recover {safe_session} handoff",
    }
    try:
        packet["recent_signatures"] = list(dict.fromkeys(
            event.arg_hash for event in events[-4:] if event.arg_hash
        ))
        progress = read_budget_state(session_id).get("last_progress")
        if isinstance(progress, dict):
            packet["last_progress"] = progress
    except Exception:
        pass
    return packet


def _format_recovery(packet: dict) -> str:
    try:
        return (
            "\n\nRecovery packet:\n"
            f"- Detector: {packet['detector']}\n"
            f"- Exact signature: {packet['signature']}\n"
            f"- Next: {packet['next_action']}\n"
            f"- Reset detector state: `{packet['reset_command']}`\n"
            f"- Fresh-session handoff: `{packet['handoff_command']}`"
        )
    except Exception:
        return ""


def evaluate(
    session_id: str,
    config: dict,
    candidate: Optional[ToolEvent] = None,
) -> Verdict:
    try:
        mode = _mode(config)
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
            det_mode = _mode(config, det.name)
            if det_mode == "off":
                continue
            if not _enabled(config, det.name):  # central enable gate
                continue
            try:
                v = det.evaluate(events, candidate, config)
            except Exception:
                continue  # a broken detector never blocks a call
            if not v:
                continue
            if det.name in _ADVISORY_ONLY and v.action == BLOCK:
                v = Verdict(NUDGE, v.detector, v.reason, response="advise")
            elif v.action == BLOCK:
                v.response = "tripwire"
            elif v.action == NUDGE:
                v.response = "advise"
            if det_mode != "enforce" and v.action == BLOCK:
                v = Verdict(
                    NUDGE, v.detector, v.reason,
                    would_block=True, response="tripwire",
                )
            elif v.action == BLOCK:
                try:
                    v.recovery = _recovery_packet(session_id, v, candidate, events)
                    v.reason += _format_recovery(v.recovery)
                except Exception:
                    pass
            if v.rank > best.rank:
                best = v

        return best
    except Exception:
        return _allow()
