#!/usr/bin/env python3
"""Codex PostToolUse adapter - records the outcome of each observed tool call.

Codex sends PostToolUse after supported tools complete, including non-zero
Bash exits when that shell path emits hooks. The result shape is tool-specific,
so error detection here is a conservative best-effort shim over common
Codex/Bash/MCP fields.

FAIL-OPEN: recording never blocks and always exits 0.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agent_rails.core.api import record  # noqa: E402


def _is_nonzero(v) -> bool:
    if isinstance(v, bool):
        return False
    try:
        return int(v) != 0
    except (TypeError, ValueError):
        return False


def _looks_like_error(result) -> bool:
    if isinstance(result, dict):
        if result.get("is_error") is True or result.get("isError") is True:
            return True
        if result.get("success") is False or result.get("ok") is False:
            return True
        for key in ("exit_code", "exitCode", "returncode", "return_code"):
            if key in result and _is_nonzero(result.get(key)):
                return True
        status = result.get("status")
        if isinstance(status, str) and status.strip().lower() in {
            "error", "failed", "failure",
        }:
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
        return 0

    try:
        session_id = str(payload.get("session_id", "default"))
        tool = str(payload.get("tool_name", "unknown"))
        tool_input = payload.get("tool_input", {})
        cwd = payload.get("cwd")
        result = payload.get("tool_response", payload.get("tool_output"))

        record(session_id, tool, tool_input, not _looks_like_error(result), project_dir=cwd)
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
