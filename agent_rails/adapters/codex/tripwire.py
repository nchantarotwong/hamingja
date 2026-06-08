#!/usr/bin/env python3
"""Codex PreToolUse adapter - the tripwire.

Reads Codex hook JSON on stdin, asks the shared core API whether the pending
tool call looks like flailing, and emits Codex's PreToolUse hook response.

FAIL-OPEN: any error path prints nothing and exits 0, so the call proceeds.
"""
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agent_rails.core.api import check  # noqa: E402
from agent_rails.detectors.base import BLOCK, NUDGE  # noqa: E402


_ESCALATED_WRAPPERS = {
    "pr-create",
    "pr-merge",
    "post-merge-cleanup",
    "ci-status",
    "ci-failures",
}


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


def _dig(obj, *keys):
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _command_from(tool_input) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for path in (
        ("command",), ("cmd",),
        ("parameters", "command"), ("parameters", "cmd"),
        ("arguments", "command"), ("arguments", "cmd"),
        ("args", "command"), ("args", "cmd"),
    ):
        val = _dig(tool_input, *path)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _sandbox_permissions(tool_input) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for path in (
        ("sandbox_permissions",),
        ("parameters", "sandbox_permissions"),
        ("arguments", "sandbox_permissions"),
        ("args", "sandbox_permissions"),
    ):
        val = _dig(tool_input, *path)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _contains_agent_rails_wrapper(command: str) -> str:
    try:
        tokens = [tok.lower() for tok in shlex.split(command)]
    except ValueError:
        return ""
    for i, tok in enumerate(tokens[:-1]):
        if tok.rsplit("/", 1)[-1] != "agent-rails":
            continue
        sub = tokens[i + 1]
        if sub in _ESCALATED_WRAPPERS:
            return sub
    return ""


def _codex_escalation_context(tool: str, tool_input) -> str:
    if str(tool) != "Bash":
        return ""
    if _sandbox_permissions(tool_input) == "require_escalated":
        return ""
    command = _command_from(tool_input)
    wrapper = _contains_agent_rails_wrapper(command)
    if not wrapper:
        return ""
    return (
        "Codex sandbox preflight: this agent-rails wrapper usually needs "
        "network and/or .git writes. Run it with "
        "`sandbox_permissions=\"require_escalated\"` now instead of first "
        "letting it fail in the sandbox. Wrapper: "
        f"`agent-rails {wrapper}`."
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    try:
        session_id = str(payload.get("session_id", "default"))
        tool = str(payload.get("tool_name", "unknown"))
        tool_input = payload.get("tool_input", {})
        cwd = payload.get("cwd")

        verdict = check(session_id, tool, tool_input, project_dir=cwd)
        try:
            escalation_context = _codex_escalation_context(tool, tool_input)
        except Exception:
            escalation_context = ""

        if verdict.action == BLOCK:
            _emit_deny(verdict.reason)
        elif verdict.action == NUDGE:
            context = verdict.reason
            if getattr(verdict, "would_block", False):
                context = (
                    "[observe] This call WOULD BE BLOCKED if this detector "
                    "were enforcing. " + context
                )
            if escalation_context:
                context = context + "\n\n" + escalation_context
            _emit_nudge(context)
        elif escalation_context:
            _emit_nudge(escalation_context)
    except Exception:
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
