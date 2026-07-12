"""Fail-open, session-scoped delegation lifecycle state."""
from __future__ import annotations

import json
from pathlib import Path

from .state import _lock, _state_dir, _unlock


def _path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session_id))
    return _state_dir() / f"{safe or 'default'}-delegation.json"


def _text(value, limit: int = 256) -> str:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else ""


def record_lifecycle(payload: object) -> dict:
    """Record a proven SubagentStart/Stop event and return bounded state."""
    try:
        if not isinstance(payload, dict):
            return {}
        event = payload.get("hook_event_name")
        if event not in {"SubagentStart", "SubagentStop"}:
            return {}
        session_id = _text(payload.get("session_id"))
        agent_id = _text(payload.get("agent_id"))
        agent_type = _text(payload.get("agent_type"))
        if not session_id or not agent_id or not agent_type:
            return {}
        path = _path(session_id)
        with path.open("a+", encoding="utf-8") as fh:
            _lock(fh, exclusive=True)
            try:
                fh.seek(0)
                try:
                    data = json.loads(fh.read() or "{}")
                except Exception:
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                active = data.get("active")
                if not isinstance(active, dict):
                    active = {}
                active = {
                    str(key)[:256]: value
                    for key, value in list(active.items())[-64:]
                    if isinstance(key, str) and isinstance(value, dict)
                }
                started = data.get("started", 0)
                completed = data.get("completed", 0)
                started = started if isinstance(started, int) and started >= 0 else 0
                completed = completed if isinstance(completed, int) and completed >= 0 else 0
                if event == "SubagentStart":
                    if agent_id not in active:
                        started += 1
                    active[agent_id] = {
                        "agent_type": agent_type,
                        "turn_id": _text(payload.get("turn_id")),
                    }
                    if len(active) > 64:
                        active = dict(list(active.items())[-64:])
                else:
                    if agent_id in active:
                        completed += 1
                    active.pop(agent_id, None)
                state = {
                    "session_id": session_id,
                    "active": active,
                    "active_children": len(active),
                    "started": started,
                    "completed": completed,
                }
                fh.seek(0)
                fh.truncate()
                fh.write(json.dumps(state, sort_keys=True))
                fh.flush()
                return state
            finally:
                _unlock(fh)
    except Exception:
        return {}


def read_state(session_id: str) -> dict:
    try:
        path = _path(session_id)
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as fh:
            raw = fh.read(131_073)
        if len(raw) > 131_072:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def reset_state(session_id: str) -> bool:
    try:
        path = _path(session_id)
        if not path.exists():
            return False
        path.unlink()
        return True
    except Exception:
        return False
