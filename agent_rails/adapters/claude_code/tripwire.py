#!/usr/bin/env python3
"""Claude Code PreToolUse adapter — the tripwire.

Wired as a PreToolUse hook. Reads the hook payload on stdin, builds a
candidate ToolEvent for the call that's about to run, evaluates it against the
session's recent history, and emits the appropriate Claude Code hook response:

    BLOCK  -> permissionDecision "deny" with the reason (enforce mode only)
    NUDGE  -> additionalContext only (advisory, the call still proceeds)
    ALLOW  -> empty output

Output contract (per code.claude.com/docs/en/hooks):
    deny:  {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "permissionDecision": "deny", "permissionDecisionReason": "..."}}
    nudge: {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "additionalContext": "..."}}   # no permissionDecision => normal flow

FAIL-OPEN: any error path prints nothing and exits 0, so the call proceeds.
The tripwire can never be the reason a session stalls.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agent_rails.config import load_config  # noqa: E402
from agent_rails.core.engine import evaluate  # noqa: E402
from agent_rails.core.events import PENDING, ToolEvent, hash_args  # noqa: E402
from agent_rails.detectors.base import BLOCK, NUDGE  # noqa: E402


def _emit_deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


def _emit_nudge(context: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": context,
        }
    }))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # unparseable -> allow

    try:
        session_id = str(payload.get("session_id", "default"))
        tool = str(payload.get("tool_name", "unknown"))
        tool_input = payload.get("tool_input", {})
        cwd = payload.get("cwd")

        cfg = load_config(cwd)
        candidate = ToolEvent(session_id, tool, hash_args(tool_input), PENDING, time.time())
        verdict = evaluate(session_id, cfg, candidate=candidate)

        if verdict.action == BLOCK:
            _emit_deny(verdict.reason)
        elif verdict.action == NUDGE:
            _emit_nudge(verdict.reason)
        # ALLOW: print nothing
    except Exception:
        return 0  # any failure -> allow

    return 0


if __name__ == "__main__":
    sys.exit(main())
