"""Static, versioned runtime capability declarations.

Declarations are promises established by adapter fixtures. Runtime probes may
downgrade a claim for the current session, but callers must never upgrade one.
"""
from __future__ import annotations

from copy import deepcopy


_MANIFESTS = {
    "codex": {
        "version": 2,
        "runtime": "codex",
        "pre_tool_enforcement": "partial",
        "post_tool_outcomes": "partial",
        "quota_probe": True,
        "quota_ttl_seconds": 300,
        "context_probe": True,
        "delegation_spawn": True,
        "delegation_completion": True,
        "delegation_identity": True,
        "delegation_lineage": False,
        "delegation_fallback": "monotonic_grants",
    },
    "claude_code": {
        "version": 2,
        "runtime": "claude_code",
        "pre_tool_enforcement": "full",
        "post_tool_outcomes": "full",
        "quota_probe": False,
        "quota_ttl_seconds": None,
        "context_probe": True,
        "delegation_spawn": True,
        "delegation_completion": True,
        "delegation_identity": True,
        "delegation_lineage": False,
        "delegation_fallback": "monotonic_grants",
    },
}


def manifest(runtime: str, downgrades: dict | None = None) -> dict:
    """Return a copy of a first-class manifest, with fail-open downgrades only."""
    try:
        base = deepcopy(_MANIFESTS[str(runtime)])
        if not isinstance(downgrades, dict):
            return base
        for key, value in downgrades.items():
            if key not in base or key in {"version", "runtime"}:
                continue
            current = base[key]
            if isinstance(current, bool) and value is False:
                base[key] = False
            elif current == "full" and value in {"partial", "none"}:
                base[key] = value
            elif current == "partial" and value == "none":
                base[key] = value
        return base
    except Exception:
        return {}


def delegation_observation(runtime: str, payload: object) -> dict | None:
    """Return only delegation facts the runtime payload actually proves."""
    try:
        if not isinstance(payload, dict):
            return None
        if runtime in {"codex", "claude_code"} and payload.get("hook_event_name") in {
            "SubagentStart", "SubagentStop",
        }:
            agent_id = payload.get("agent_id")
            agent_type = payload.get("agent_type")
            session_id = payload.get("session_id")
            if (not isinstance(agent_id, str) or not agent_id
                    or not isinstance(agent_type, str) or not agent_type
                    or not isinstance(session_id, str) or not session_id):
                return None
            return {
                "event": "spawn" if payload["hook_event_name"] == "SubagentStart" else "complete",
                "agent_id": agent_id,
                "agent_type": agent_type,
                "session_id": session_id,
                "turn_id": payload.get("turn_id") if isinstance(payload.get("turn_id"), str) else "",
                "spawn_observed": payload["hook_event_name"] == "SubagentStart",
                "identity_observed": True,
                "completion_observed": payload["hook_event_name"] == "SubagentStop",
                "lineage_observed": False,
                "enforcement": "session_concurrency_advisory",
            }
        return None
    except Exception:
        return None
