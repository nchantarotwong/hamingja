#!/usr/bin/env python3
"""Codex/Claude SubagentStart/Stop recorder; advisory only and fail-open."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hamingja.config import load_config  # noqa: E402
from hamingja.core.delegation import record_lifecycle  # noqa: E402


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        cfg = load_config(payload.get("cwd")) if isinstance(payload, dict) else {}
        if isinstance(cfg, dict) and cfg.get("mode") == "off":
            print("{}")
            return 0
        state = record_lifecycle(payload)
        output = {}
        if payload.get("hook_event_name") == "SubagentStart" and state:
            delegation = cfg.get("delegation", {}) if isinstance(cfg, dict) else {}
            limit = max(1, int(delegation.get("max_active_children", 1)))
            if state.get("active_children", 0) > limit:
                message = (
                    f"hamingja delegation advisory: {state['active_children']} active "
                    f"children exceeds the configured session limit {limit}. "
                    "Do not spawn another child; finish or stop an existing child first."
                )
                output = {"systemMessage": message, "hookSpecificOutput": {
                    "hookEventName": "SubagentStart",
                    "additionalContext": message,
                }}
        print(json.dumps(output))
    except Exception:
        print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
