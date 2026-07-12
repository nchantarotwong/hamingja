"""Detector interface + Verdict type.

A detector inspects a window of recent ToolEvents (plus an optional candidate
call that is about to run) and optionally returns a Verdict. Adding a new
guardrail is exactly: subclass Detector, implement evaluate(), register it in
the engine's DETECTORS list. Whether a detector is *enabled* is decided
centrally by the engine — a detector does not have to remember to check it.

Detectors must never raise for control flow — the engine wraps each call and
treats an exception as "no verdict" (fail-open), but a detector that raises on
every call is silently useless, so keep evaluate() total.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..core.events import ToolEvent

# actions, ordered by severity
ALLOW = "allow"
NUDGE = "nudge"  # advisory injected into context; does NOT block the call
BLOCK = "block"  # the call is denied until the agent changes course

_RANK = {ALLOW: 0, NUDGE: 1, BLOCK: 2}


@dataclass
class Verdict:
    action: str  # ALLOW | NUDGE | BLOCK — the EFFECTIVE action to take
    detector: str
    reason: str
    would_block: bool = False  # True when a BLOCK was downgraded by observe mode
    response: str = "observe"  # response shape, separate from authority/action

    @property
    def rank(self) -> int:
        return _RANK.get(self.action, 0)


class Detector:
    name = "detector"

    def evaluate(
        self,
        events: list[ToolEvent],
        candidate: Optional[ToolEvent],
        config: dict,
    ) -> Optional[Verdict]:
        """Return a Verdict if this detector fires, else None.

        events:    recent history, oldest first (the candidate is NOT included).
        candidate: the call about to run (PreToolUse), or None when evaluating
                   purely on history.
        config:    the merged, already-sanitized config dict.
        """
        raise NotImplementedError

    def _cfg(self, config: dict) -> dict:
        return (config.get("detectors", {}) or {}).get(self.name, {}) or {}
