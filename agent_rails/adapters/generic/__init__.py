"""Generic adapter — import this into any custom agent loop.

For harnesses without native hooks (or your own orchestration code), call
these two functions directly:

    from agent_rails.adapters.generic import observe, check

    # BEFORE running a tool call:
    verdict = check(session_id, tool_name, tool_args)
    if verdict.action == "block":
        # refuse / re-plan; verdict.reason explains why
        ...
    elif verdict.action == "nudge":
        # inject verdict.reason into the model's context, then proceed
        ...

    # AFTER the tool call completes:
    observe(session_id, tool_name, tool_args, ok=succeeded)

`check` is read-only; `observe` records. Keeping them separate lets a
PreToolUse-style hook evaluate the *candidate* call against history before it
runs, then a PostToolUse-style hook record the outcome.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from ...config import load_config
from ...core.engine import evaluate
from ...core.events import ERROR, OK, PENDING, ToolEvent, hash_args
from ...core.state import append_event
from ...detectors.base import ALLOW, Verdict


def observe(
    session_id: str,
    tool: str,
    args: Any,
    ok: bool = True,
    project_dir: Optional[str] = None,
) -> None:
    """Record the outcome of a completed tool call. Never raises."""
    try:
        ev = ToolEvent(session_id, tool, hash_args(args), OK if ok else ERROR, time.time())
        append_event(ev)
    except Exception:
        return


def check(
    session_id: str,
    tool: str,
    args: Any,
    project_dir: Optional[str] = None,
) -> Verdict:
    """Evaluate a candidate call against recent history. Fail-open: ALLOW on error."""
    try:
        cfg = load_config(project_dir)
        candidate = ToolEvent(session_id, tool, hash_args(args), PENDING, time.time())
        return evaluate(session_id, cfg, candidate=candidate)
    except Exception:
        return Verdict(ALLOW, "generic", "")
