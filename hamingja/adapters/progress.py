"""Adapter-owned admission of structured workflow lifecycle progress."""
from __future__ import annotations

import json
import shlex
from pathlib import PurePath

from ..core.api import record_progress
from ..core.events import ToolEvent
from ..core.state import read_recent


_DURABLE = {
    ("ci_status", "ready"): ("pending_or_unknown", "ready"),
    ("pr_create", "created"): ("absent", "created"),
    ("pr_merge", "merged"): ("open_or_unknown", "merged"),
}


def _command_from(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("command", "cmd"):
        item = value.get(key)
        if isinstance(item, str):
            return item
    for key in ("parameters", "args", "input"):
        nested = value.get(key)
        command = _command_from(nested)
        if command:
            return command
    return ""


def _standalone_wrapper(tool_input: object, operation: object) -> bool:
    try:
        command = _command_from(tool_input).strip()
        if not command or any(token in command for token in (";", "|", "&", "`", "$", ">", "<", "\n")):
            return False
        parts = shlex.split(command)
        index = 0
        if parts and PurePath(parts[0]).name == "timeout":
            if len(parts) < 3 or not parts[1].rstrip("smh").replace(".", "", 1).isdigit():
                return False
            index = 2
        if index >= len(parts) or PurePath(parts[index]).name != "hamingja":
            return False
        expected = {"ci_status": "ci-status", "pr_create": "pr-create", "pr_merge": "pr-merge"}.get(operation)
        return expected is not None and parts[index + 1] == expected and "--json" in parts[index + 2:]
    except Exception:
        return False


def record_workflow_progress(
    session_id: str,
    tool: str,
    tool_input: object,
    result: object,
    project_dir=None,
) -> bool:
    """Credit only versioned lifecycle JSON anchored to the just-recorded event."""
    try:
        if not isinstance(result, dict):
            return False
        raw = result.get("stdout", result.get("output"))
        if not isinstance(raw, str) or not raw.strip():
            return False
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return False
        operation = payload.get("operation")
        state = payload.get("state")
        transition = _DURABLE.get((operation, state))
        if transition is None:
            return False
        events = read_recent(session_id, 1)
        if not events:
            return False
        event = events[-1]
        if str(tool) != "Bash":
            return False
        if event.arg_hash != ToolEvent.candidate(session_id, tool, tool_input).arg_hash:
            return False
        if not _standalone_wrapper(tool_input, operation):
            return False
        anchor = event.arg_hash
        return record_progress(session_id, {
            "kind": "workflow_transition",
            "anchor": anchor,
            "validation_id": f"{operation}:{anchor}",
            "state_before": transition[0],
            "state_after": transition[1],
        }, project_dir=project_dir)
    except Exception:
        return False
