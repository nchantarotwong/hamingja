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

from agent_rails.adapters.read_advisory import large_read_advisory, large_read_line_count  # noqa: E402
from agent_rails.core.api import check  # noqa: E402
from agent_rails.core.budget import BLOCK as BUDGET_BLOCK  # noqa: E402
from agent_rails.core.budget import NUDGE as BUDGET_NUDGE  # noqa: E402
from agent_rails.core.budget import increment_and_check as budget_check  # noqa: E402
from agent_rails.detectors.base import BLOCK, NUDGE  # noqa: E402
from agent_rails.ledger import advisory_for_tool as ledger_advisory  # noqa: E402
from agent_rails.ledger import discover_root as ledger_root  # noqa: E402


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


def _is_budget_command(tool: str, tool_input) -> bool:
    """True for Bash calls that are agent-rails budget commands.

    Exempt from the budget gate so the agent can self-approve or query status
    without consuming a metered slot that triggers another checkpoint block.
    Mirrors the Claude adapter, but resolves the command through Codex's several
    possible arg shapes.
    """
    if str(tool).strip() != "Bash":
        return False
    try:
        tokens = shlex.split(_command_from(tool_input))
    except ValueError:
        return False
    for i, tok in enumerate(tokens[:-1]):
        if tok.rsplit("/", 1)[-1] == "agent-rails" and tokens[i + 1] == "budget":
            return True
    return False


def _budget_tool_name(tool: str, tool_input) -> str:
    """Return the tool name to meter for this call.

    Recognized agent-rails ledger commands get command-family names so the
    budget layer can discount guardrail bookkeeping without exempting all Bash.
    Any parse failure or unrecognized command falls back to the original tool.
    """
    try:
        if str(tool).strip() != "Bash":
            return str(tool)
        tokens = shlex.split(_command_from(tool_input))
        for i, tok in enumerate(tokens[:-2]):
            if tok.rsplit("/", 1)[-1] == "agent-rails" and tokens[i + 1] == "ledger":
                action = tokens[i + 2]
                if action in {"add", "check", "relevant", "retire", "reverify"}:
                    return f"Bash:agent-rails ledger {action}"
                return str(tool)
        return str(tool)
    except Exception:
        return str(tool)


def _read_quota_safe(session_id: str):
    """Fetch the real Codex quota reading, fail-open to None.

    Isolated so a probe error (missing rollout, parse failure) can never affect
    the gate — the budget check simply falls back to the call-count-only path.
    """
    try:
        from agent_rails.adapters.codex.quota import read_quota  # noqa: PLC0415
        return read_quota(session_id)
    except Exception:
        return None


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


def _ledger_context(tool: str, tool_input, cwd) -> str:
    try:
        root = ledger_root(Path(cwd) if cwd else Path.cwd())
        return ledger_advisory(root, tool, tool_input)
    except Exception:
        return ""


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

        try:
            read_advisory = large_read_advisory(tool, tool_input) or ""
        except Exception:
            read_advisory = ""
        ledger_context = _ledger_context(tool, tool_input, cwd)

        if verdict.action == BLOCK:
            _emit_deny(verdict.reason)
            return 0

        # --- budget gate ---
        # Runs after detectors so a detector block takes priority. Fed the real
        # Codex quota reading so a low subscription window defers the soft
        # checkpoint instead of blocking on a proxy call count. Always fails open.
        budget_nudge = ""
        try:
            from agent_rails.config import load_config  # noqa: PLC0415
            cfg = load_config(cwd)
            if cfg.get("mode") != "off":
                budget_cfg = cfg.get("budget")
                if isinstance(budget_cfg, dict) and budget_cfg.get("enabled", True):
                    if not _is_budget_command(tool, tool_input):
                        try:
                            is_large = large_read_line_count(tool, tool_input) > 0
                        except Exception:
                            is_large = False
                        reading = _read_quota_safe(session_id)
                        budget_tool = _budget_tool_name(tool, tool_input)
                        bv = budget_check(
                            session_id, budget_tool, is_large, budget_cfg,
                            quota_reading=reading,
                            mechanical_signal=bool(getattr(verdict, "would_block", False)),
                        )
                        if bv.action == BUDGET_BLOCK:
                            _emit_deny(bv.reason)
                            return 0
                        if bv.action == BUDGET_NUDGE:
                            budget_nudge = bv.reason
        except Exception:
            pass  # budget gate always fails open

        # Assemble advisory nudges. A detector or budget nudge carries the
        # escalation/read advisories with it; with neither, the original
        # escalation-else-read behavior is preserved.
        nudge_parts: list[str] = []
        if verdict.action == NUDGE:
            context = verdict.reason
            if getattr(verdict, "would_block", False):
                context = (
                    "[observe] This call WOULD BE BLOCKED if this detector "
                    "were enforcing. " + context
                )
            nudge_parts.append(context)
        if budget_nudge:
            nudge_parts.append(budget_nudge)

        if nudge_parts:
            if escalation_context:
                nudge_parts.append(escalation_context)
            if ledger_context:
                nudge_parts.append(ledger_context)
            if read_advisory:
                nudge_parts.append(read_advisory)
            _emit_nudge("\n\n".join(nudge_parts))
        elif escalation_context:
            _emit_nudge(escalation_context)
        elif ledger_context:
            _emit_nudge(ledger_context)
        elif read_advisory:
            _emit_nudge(read_advisory)
    except Exception:
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
