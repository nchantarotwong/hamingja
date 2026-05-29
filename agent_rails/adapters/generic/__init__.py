"""Generic adapter — import this into any custom agent loop.

These are thin wrappers over the shared core API (agent_rails.core.api). They
exist so a custom loop reads naturally; all logic and the fail-open guard live
in the core, not here.

    from agent_rails.adapters.generic import observe, check

    # BEFORE running a tool call:
    verdict = check(session_id, tool_name, tool_args)
    if verdict.action == "block":
        ...                      # refuse / re-plan; verdict.reason explains why
    elif verdict.action == "nudge":
        ...                      # inject verdict.reason into context, then proceed
        # verdict.would_block is True if observe mode downgraded a real block

    # AFTER the tool call completes:
    observe(session_id, tool_name, tool_args, ok=succeeded)
"""
from __future__ import annotations

from typing import Any, Optional

from ...core.api import check as _check
from ...core.api import record as _record
from ...detectors.base import Verdict


def observe(
    session_id: str,
    tool: str,
    args: Any,
    ok: bool = True,
    project_dir: Optional[str] = None,
) -> None:
    """Record the outcome of a completed tool call."""
    _record(session_id, tool, args, ok, project_dir)


def check(
    session_id: str,
    tool: str,
    args: Any,
    project_dir: Optional[str] = None,
) -> Verdict:
    """Evaluate a candidate call against recent history."""
    return _check(session_id, tool, args, project_dir)
