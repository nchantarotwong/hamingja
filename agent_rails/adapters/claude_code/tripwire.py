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

from agent_rails.adapters.read_advisory import large_read_advisory, large_read_line_count  # noqa: E402
from agent_rails.core.api import check  # noqa: E402
from agent_rails.core.budget import BLOCK as BUDGET_BLOCK  # noqa: E402
from agent_rails.core.budget import NUDGE as BUDGET_NUDGE  # noqa: E402
from agent_rails.core.budget import increment_and_check as budget_check  # noqa: E402
from agent_rails.detectors.base import BLOCK, NUDGE  # noqa: E402
from agent_rails.ledger import advisory_for_tool as ledger_advisory  # noqa: E402
from agent_rails.ledger import discover_root as ledger_root  # noqa: E402


def _is_budget_command(tool: str, tool_input: object) -> bool:
    """Return True for Bash calls that are agent-rails budget commands.

    These are exempt from the budget gate so the agent can self-approve or
    query status without itself consuming a metered slot that triggers another
    checkpoint block.
    """
    if str(tool).strip() != "Bash":
        return False
    if not isinstance(tool_input, dict):
        return False
    cmd = str(tool_input.get("command") or "").lstrip()
    return cmd.startswith("agent-rails budget")


def _read_quota_safe(session_id: str, cwd, budget_cfg: dict):
    """Fetch the Claude context-fill reading, fail-open to None.

    Isolated so a probe error (missing transcript, parse failure) can never
    affect the gate — the budget check falls back to the call-count-only path.
    Claude has no persisted rate-limit signal, so this reading carries only
    context occupancy (an advisory nudge, never checkpoint relief).
    """
    try:
        from agent_rails.adapters.claude_code.quota import read_quota  # noqa: PLC0415
        window = 200_000
        if isinstance(budget_cfg, dict):
            window = budget_cfg.get("context_window_tokens", window)
        return read_quota(session_id, cwd, window)
    except Exception:
        return None


def _large_read_line_count(tool: str, args: object) -> int:
    """Compatibility wrapper for existing tests/imports."""
    return large_read_line_count(tool, args)


def _large_read_advisory(tool: str, args: object) -> str | None:
    """Compatibility wrapper for existing tests/imports."""
    return large_read_advisory(tool, args)


def _ledger_advisory(tool: str, args: object, cwd) -> str:
    try:
        root = ledger_root(Path(cwd) if cwd else Path.cwd())
        return ledger_advisory(root, tool, args)
    except Exception:
        return ""


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

        # --- detector check (existing logic) ---
        verdict = check(session_id, tool, tool_input, project_dir=cwd)
        if verdict.action == BLOCK:
            _emit_deny(verdict.reason)
            return 0

        # --- budget gate ---
        # Runs after detectors so a detector block takes priority.
        # Enforces regardless of observe/enforce mode; respects mode=off only.
        nudge_parts: list[str] = []
        if verdict.action == NUDGE:
            reason = verdict.reason
            if getattr(verdict, "would_block", False):
                reason = (
                    "[observe] This call WOULD BE BLOCKED if this detector "
                    "were enforcing. " + reason
                )
            nudge_parts.append(reason)

        try:
            from agent_rails.config import load_config  # noqa: PLC0415
            cfg = load_config(cwd)
            if cfg.get("mode") != "off":
                budget_cfg = cfg.get("budget")
                if isinstance(budget_cfg, dict) and budget_cfg.get("enabled", True):
                    if not _is_budget_command(tool, tool_input):
                        is_large = _large_read_line_count(tool, tool_input) > 0
                        reading = _read_quota_safe(session_id, cwd, budget_cfg)
                        bv = budget_check(
                            session_id, tool, is_large, budget_cfg, quota_reading=reading
                        )
                        if bv.action == BUDGET_BLOCK:
                            _emit_deny(bv.reason)
                            return 0
                        if bv.action == BUDGET_NUDGE:
                            nudge_parts.append(bv.reason)
        except Exception:
            pass  # budget gate always fails open

        if nudge_parts:
            ledger_context = _ledger_advisory(tool, tool_input, cwd)
            if ledger_context:
                nudge_parts.append(ledger_context)
            _emit_nudge("\n\n".join(nudge_parts))
            return 0

        # ALLOW: emit pre-read advisory for unscoped large-file reads
        ledger_context = _ledger_advisory(tool, tool_input, cwd)
        if ledger_context:
            _emit_nudge(ledger_context)
            return 0
        advisory = _large_read_advisory(tool, tool_input)
        if advisory:
            _emit_nudge(advisory)

    except Exception:
        return 0  # any failure -> allow

    return 0


if __name__ == "__main__":
    sys.exit(main())
