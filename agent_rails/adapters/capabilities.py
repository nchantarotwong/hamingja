"""Static, versioned runtime capability declarations.

Declarations are promises established by adapter fixtures. Runtime probes may
downgrade a claim for the current session, but callers must never upgrade one.
"""
from __future__ import annotations

from copy import deepcopy


_MANIFESTS = {
    "codex": {
        "version": 1,
        "runtime": "codex",
        "pre_tool_enforcement": "partial",
        "post_tool_outcomes": "partial",
        "quota_probe": True,
        "quota_ttl_seconds": 300,
        "context_probe": True,
        "delegation_spawn": False,
        "delegation_completion": False,
        "delegation_lineage": False,
    },
    "claude_code": {
        "version": 1,
        "runtime": "claude_code",
        "pre_tool_enforcement": "full",
        "post_tool_outcomes": "full",
        "quota_probe": False,
        "quota_ttl_seconds": None,
        "context_probe": True,
        "delegation_spawn": True,
        "delegation_completion": False,
        "delegation_lineage": False,
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
