#!/usr/bin/env python3
"""Claude Code PostToolUse adapter — records the outcome of each tool call.

Wired as a PostToolUse hook. Reads the hook payload on stdin, translates it
into a ToolEvent, and appends it to the session log. It never blocks anything
and always exits 0 — recording is pure observation.

Claude Code PostToolUse stdin (per code.claude.com/docs/en/hooks):
    { session_id, transcript_path, cwd, permission_mode,
      hook_event_name: "PostToolUse", tool_name, tool_input, tool_output }

Error detection is best-effort and conservative: we only mark a call as an
error when the output clearly says so (success == False, or a non-empty
`error`). Anything ambiguous counts as OK, so we never manufacture a phantom
error streak.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# make `agent_rails` importable when run as a standalone hook script
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agent_rails.core.events import ERROR, OK, ToolEvent, hash_args  # noqa: E402
from agent_rails.core.state import append_event  # noqa: E402


def _is_error(tool_output) -> bool:
    if isinstance(tool_output, dict):
        if tool_output.get("success") is False:
            return True
        err = tool_output.get("error")
        if isinstance(err, str) and err.strip():
            return True
        if err is True:
            return True
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open: unparseable payload, record nothing

    try:
        session_id = str(payload.get("session_id", "default"))
        tool = str(payload.get("tool_name", "unknown"))
        tool_input = payload.get("tool_input", {})
        tool_output = payload.get("tool_output", payload.get("tool_response"))
        status = ERROR if _is_error(tool_output) else OK
        append_event(ToolEvent(session_id, tool, hash_args(tool_input), status, time.time()))
    except Exception:
        pass  # never let recording surface an error to the agent

    return 0


if __name__ == "__main__":
    sys.exit(main())
