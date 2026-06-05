#!/usr/bin/env python3
"""Claude Code PreToolUse adapter — the tripwire.

Wired as a PreToolUse hook. Reads the hook payload on stdin, asks the shared
core API whether the call about to run is flailing, and emits the appropriate
Claude Code hook response:

    BLOCK  -> permissionDecision "deny" with the reason (enforce mode only)
    NUDGE  -> additionalContext only (advisory, the call still proceeds);
              when this was a block downgraded by observe mode
              (verdict.would_block), the context says so.
    ALLOW  -> empty output

Output contract (per code.claude.com/docs/en/hooks):
    deny:  {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "permissionDecision": "deny", "permissionDecisionReason": "..."}}
    nudge: {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "additionalContext": "..."}}   # no permissionDecision => normal flow

FAIL-OPEN: any error path prints nothing and exits 0, so the call proceeds.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agent_rails.core.api import check  # noqa: E402
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

        verdict = check(session_id, tool, tool_input, project_dir=cwd)

        if verdict.action == BLOCK:
            _emit_deny(verdict.reason)
        elif verdict.action == NUDGE:
            context = verdict.reason
            if getattr(verdict, "would_block", False):
                context = (
                    "[observe] This call WOULD BE BLOCKED if this detector "
                    "were enforcing. " + context
                )
            _emit_nudge(context)
        # ALLOW: print nothing
    except Exception:
        return 0  # any failure -> allow

    return 0


if __name__ == "__main__":
    sys.exit(main())
