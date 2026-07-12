"""Verdict audit log — the observability layer behind `observe` mode.

`observe` mode is the safe-rollout story: run it first, watch what *would* be
blocked, tune thresholds, then flip to `enforce`. That story only works if the
would-blocks are visible. A nudge is injected into the agent's context and then
gone; nobody can tune against signal they can't see. So every non-ALLOW verdict
is appended here, to one global JSONL log, and `agent-rails report` aggregates
it into fire rates per detector.

This log is for the OPERATOR, not the agent: it never feeds back into a
verdict, so it can't change what gets blocked. Like everything else here it is
FAIL-OPEN — any error degrades to a no-op or an empty read; auditing can never
be the reason a tool call is blocked or a hook crashes.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .state import _lock, _state_dir, _unlock  # reuse the same advisory locking
from ..detectors.base import ALLOW, Verdict


def _audit_file() -> Path:
    return _state_dir() / "_audit.jsonl"


def log_verdict(session_id: str, tool: str, verdict: Verdict, cap: int = 5000) -> None:
    """Append a non-ALLOW verdict to the global audit log. Never raises.

    ALLOW is the overwhelming common case and carries no tuning signal, so it
    is not logged — keeping the file small and its contents all-signal.
    """
    try:
        if verdict is None or verdict.action == ALLOW:
            return
        entry = {
            "ts": time.time(),
            "session_id": session_id,
            "tool": tool,
            "detector": verdict.detector,
            "action": verdict.action,
            "would_block": bool(getattr(verdict, "would_block", False)),
            "response": str(getattr(verdict, "response", "observe")),
        }
        recovery = getattr(verdict, "recovery", None)
        if isinstance(recovery, dict):
            detector = recovery.get("detector")
            signature = recovery.get("signature")
            if isinstance(detector, str) and detector:
                entry["recovery_detector"] = detector[:128]
            if isinstance(signature, str) and signature:
                entry["recovery_signature"] = signature[:1000]
        path = _audit_file()
        with path.open("a+", encoding="utf-8") as fh:
            _lock(fh, exclusive=True)
            try:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
                fh.flush()
                fh.seek(0)
                lines = fh.read().splitlines()
                if len(lines) > cap:
                    fh.seek(0)
                    fh.truncate()
                    fh.write("\n".join(lines[-cap:]) + "\n")
                    fh.flush()
            finally:
                _unlock(fh)
    except Exception:
        return


def read_audit(limit: int = 0) -> list[dict]:
    """Return audit entries (oldest first). `limit > 0` keeps the most recent N.
    [] on any error."""
    try:
        path = _audit_file()
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as fh:
            _lock(fh, exclusive=False)
            try:
                data = fh.read()
            finally:
                _unlock(fh)
        lines = data.splitlines()
        if isinstance(limit, int) and limit > 0:
            lines = lines[-limit:]
        out: list[dict] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue  # one corrupt line never sinks the read
        return out
    except Exception:
        return []


def clear_audit() -> None:
    """Delete the audit log (for `agent-rails report --reset`). Never raises."""
    try:
        _audit_file().unlink(missing_ok=True)
    except Exception:
        return


def summarize(entries: list[dict]) -> dict:
    """Aggregate raw audit entries into per-detector fire counts.

    Returns counts that answer the one question observe mode exists to answer:
    "if I flip global or detector mode to enforce, how many blocks will I get,
    and from which detector?"
    A would_block is a nudge-now/block-in-enforce; a real block only appears
    here when already running enforce.
    """
    detectors: dict[str, dict] = {}
    sessions: set = set()
    nudges = blocks = would_blocks = 0
    for e in entries:
        if not isinstance(e, dict):
            continue
        det = str(e.get("detector", "?"))
        action = str(e.get("action", ""))
        wb = bool(e.get("would_block", False))
        sessions.add(e.get("session_id"))
        d = detectors.setdefault(det, {"nudge": 0, "block": 0, "would_block": 0})
        if action == "block":
            d["block"] += 1
            blocks += 1
        elif action == "nudge":
            if wb:
                d["would_block"] += 1
                would_blocks += 1
            else:
                d["nudge"] += 1
                nudges += 1
    return {
        "total": len([e for e in entries if isinstance(e, dict)]),
        "sessions": len([s for s in sessions if s is not None]),
        "nudges": nudges,
        "would_blocks": would_blocks,
        "blocks": blocks,
        "by_detector": detectors,
    }
