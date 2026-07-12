#!/usr/bin/env python3
"""Claude Code recorder — records the outcome of each tool call.

Wired as BOTH:
  * PostToolUse        -> the call succeeded (record OK), with a payload
                          heuristic fallback in case a failure is delivered here
  * PostToolUseFailure -> the call failed (record ERROR), authoritative

Detecting errors by the EVENT is deterministic; the per-tool shape of the
result payload is undocumented, so we don't rely on parsing it. The heuristic
below is only a best-effort fallback for harness versions that route a failure
through PostToolUse.

It never blocks anything and always exits 0 — recording is pure observation.
Recording also honors mode=off / .hamingja-off (handled in core.api.record),
so an opted-out repo stays fully inert.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# make `hamingja` importable when run as a standalone hook script
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from hamingja.core.api import record  # noqa: E402
from hamingja.adapters.progress import record_workflow_progress  # noqa: E402
from hamingja.adapters.framework_progress import record_framework_progress  # noqa: E402


def _looks_like_error(result) -> bool:
    """Best-effort fallback error detection from an undocumented result shape."""
    if isinstance(result, dict):
        if result.get("is_error") is True:
            return True
        if result.get("success") is False:
            return True
        err = result.get("error")
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
        event = str(payload.get("hook_event_name", ""))
        session_id = str(payload.get("session_id", "default"))
        tool = str(payload.get("tool_name", "unknown"))
        tool_input = payload.get("tool_input", {})
        cwd = payload.get("cwd")

        result = payload.get("tool_response", payload.get("tool_output"))
        if event == "PostToolUseFailure":
            ok = False  # authoritative: this event only fires on failure
        else:
            ok = not _looks_like_error(result)

        record(session_id, tool, tool_input, ok, project_dir=cwd, output=result)
        record_workflow_progress(session_id, tool, tool_input, result, project_dir=cwd)
        record_framework_progress(session_id, tool, tool_input, result, project_dir=cwd, ok=ok)
    except Exception:
        pass  # never let recording surface an error to the agent

    return 0


if __name__ == "__main__":
    sys.exit(main())
