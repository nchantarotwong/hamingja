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
from agent_rails.core.budget import BLOCK as BUDGET_BLOCK  # noqa: E402
from agent_rails.core.budget import NUDGE as BUDGET_NUDGE  # noqa: E402
from agent_rails.core.budget import increment_and_check as budget_check  # noqa: E402
from agent_rails.detectors.base import BLOCK, NUDGE  # noqa: E402

_LARGE_FILE_LINE_THRESHOLD = 200


def _large_read_line_count(tool: str, args: object) -> int:
    """Return the line count if this is an unscoped read of a large file, else 0.

    Fails open: returns 0 on any error.
    """
    if not isinstance(args, dict):
        return 0
    if str(tool).strip().lower() != "read":
        return 0
    has_offset = args.get("offset") not in (None, "")
    has_limit = args.get("limit") not in (None, "")
    if has_offset or has_limit:
        return 0
    path_str = str(args.get("file_path") or args.get("path") or "").strip()
    if not path_str:
        return 0
    try:
        line_count = Path(path_str).read_bytes().count(b"\n")
    except OSError:
        return 0
    return line_count if line_count >= _LARGE_FILE_LINE_THRESHOLD else 0


def _large_read_advisory(tool: str, args: object) -> str | None:
    """Return an advisory string when a Read has no offset/limit on a large file."""
    line_count = _large_read_line_count(tool, args)
    if not line_count:
        return None
    path_str = str(args.get("file_path") or args.get("path") or "")  # type: ignore[union-attr]
    name = Path(path_str).name if path_str else "file"
    return (
        f"[agent-rails] {name} has ~{line_count} lines. "
        f"Prefer: grep -n for the target symbol/section, then Read with "
        f"offset+limit. Unscoped reads of large files are the primary source "
        f"of excess token usage in a session."
    )


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
                    is_large = _large_read_line_count(tool, tool_input) > 0
                    bv = budget_check(session_id, tool, is_large, budget_cfg)
                    if bv.action == BUDGET_BLOCK:
                        _emit_deny(bv.reason)
                        return 0
                    if bv.action == BUDGET_NUDGE:
                        nudge_parts.append(bv.reason)
        except Exception:
            pass  # budget gate always fails open

        if nudge_parts:
            _emit_nudge("\n\n".join(nudge_parts))
            return 0

        # ALLOW: emit pre-read advisory for unscoped large-file reads
        advisory = _large_read_advisory(tool, tool_input)
        if advisory:
            _emit_nudge(advisory)

    except Exception:
        return 0  # any failure -> allow

    return 0


if __name__ == "__main__":
    sys.exit(main())
