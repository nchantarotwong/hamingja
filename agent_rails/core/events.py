"""Normalized event schema — the harness-neutral lingua franca.

Every adapter translates its harness's native tool-call payload into a
ToolEvent before handing it to the core. The core (state, engine, detectors)
knows nothing about Claude Code, Codex, or any other harness — it only ever
sees ToolEvents.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from typing import Any

# status values
OK = "ok"
ERROR = "error"
PENDING = "pending"  # a candidate call (PreToolUse) whose outcome isn't known yet
BLOCKED = "blocked"  # an enforced block we recorded: the call was DENIED, not run


@dataclass
class ToolEvent:
    session_id: str
    tool: str
    arg_hash: str
    status: str  # OK | ERROR | PENDING
    ts: float

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @staticmethod
    def from_json(line: str) -> "ToolEvent":
        d = json.loads(line)
        return ToolEvent(
            session_id=str(d["session_id"]),
            tool=str(d["tool"]),
            arg_hash=str(d["arg_hash"]),
            status=str(d["status"]),
            ts=float(d["ts"]),
        )

    # --- factories: the ONE place a ToolEvent is built from raw args -------
    # Adapters call these instead of hand-wiring hash_args/time.time(), so a
    # schema change (new field, different hashing) touches exactly one site.

    @classmethod
    def candidate(cls, session_id: str, tool: str, args: Any) -> "ToolEvent":
        """A call about to run (PreToolUse / check); outcome unknown."""
        return cls(session_id, tool, hash_args(args), PENDING, time.time())

    @classmethod
    def record(cls, session_id: str, tool: str, args: Any, ok: bool) -> "ToolEvent":
        """A completed call with a known outcome (PostToolUse / observe)."""
        return cls(session_id, tool, hash_args(args), OK if ok else ERROR, time.time())

    @classmethod
    def blocked(cls, session_id: str, tool: str, args: Any) -> "ToolEvent":
        """A call we DENIED in enforce mode. Recorded so the history reflects the
        intervention: a blocked call never runs, so no PostToolUse follows it.
        Without this marker, a candidate-independent detector (error_streak)
        would keep blocking every subsequent call — the denied calls produce no
        success to reset the streak — and wedge the agent permanently. The
        marker is not an ERROR, so it breaks the streak and lets the agent run
        the diagnostic the block asked for; it carries the candidate's hash, so
        an identical *retry* still matches and stays blocked under repetition."""
        return cls(session_id, tool, hash_args(args), BLOCKED, time.time())


def _nonjson(o: Any):
    # Tag non-serializable values with their type so e.g. the set {1,2,3} does
    # NOT collide with the plain string "{1, 2, 3}" (which a bare repr would).
    return {"__nonjson__": type(o).__name__, "repr": repr(o)}


def hash_args(args: Any) -> str:
    """Stable short hash of tool arguments, for repetition detection.

    Identical (tool, arg_hash) recurring across calls is the strongest
    flailing signal and the one with the lowest false-positive rate: an agent
    making progress varies its calls; an agent in a doom loop repeats. We hash
    a canonical JSON form so key ordering doesn't matter, and tag any
    non-serializable value with its type so distinct values can't collapse to
    the same string. Never raises.
    """
    try:
        canonical = json.dumps(args, sort_keys=True, default=_nonjson)
    except Exception:
        canonical = repr(args)
    return hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()[:16]
