"""Normalized event schema — the harness-neutral lingua franca.

Every adapter translates its harness's native tool-call payload into a
ToolEvent before handing it to the core. The core (state, engine, detectors)
knows nothing about Claude Code, Codex, or any other harness — it only ever
sees ToolEvents.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

# status values
OK = "ok"
ERROR = "error"
PENDING = "pending"  # a candidate call (PreToolUse) whose outcome isn't known yet


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


def hash_args(args: Any) -> str:
    """Stable short hash of tool arguments, for repetition detection.

    Identical (tool, arg_hash) recurring across calls is the strongest
    flailing signal and the one with the lowest false-positive rate: an agent
    making progress varies its calls; an agent in a doom loop repeats. We hash
    a canonical JSON form so key ordering doesn't matter, and fall back to
    repr() for anything non-serializable. Never raises.
    """
    try:
        canonical = json.dumps(args, sort_keys=True, default=repr)
    except Exception:
        canonical = repr(args)
    return hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()[:16]
