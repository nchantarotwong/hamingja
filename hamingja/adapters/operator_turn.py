#!/usr/bin/env python3
"""UserPromptSubmit recorder; stores a work anchor, never prompt content."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hamingja.config import load_config  # noqa: E402
from hamingja.core.budget import mark_operator_turn  # noqa: E402


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("hook_event_name") != "UserPromptSubmit":
            print("{}")
            return 0
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            print("{}")
            return 0
        cfg = load_config(payload.get("cwd"))
        if not isinstance(cfg, dict) or cfg.get("mode") == "off":
            print("{}")
            return 0
        budget_cfg = cfg.get("budget") if isinstance(cfg, dict) else None
        if isinstance(budget_cfg, dict) and budget_cfg.get("enabled", True):
            mark_operator_turn(session_id, budget_cfg)
        print("{}")
    except Exception:
        print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
