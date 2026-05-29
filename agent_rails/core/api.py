"""The shared core API every adapter calls.

Adapters (claude_code, generic, a future codex) translate their harness's
native payload into (session_id, tool, args, ok) and call these two functions.
The evaluate/record glue AND the outer fail-open guard live HERE, once, so a
new adapter can't reintroduce a fail-closed path or a hashing mismatch by
re-implementing the sequence.

    check(...)  -> Verdict   # before a call runs (read-only)
    record(...) -> None      # after a call completes (writes state)
"""
from __future__ import annotations

from typing import Any, Optional

from .engine import evaluate
from .events import ToolEvent
from .state import append_event
from ..config import load_config
from ..detectors.base import ALLOW, Verdict


def check(
    session_id: str,
    tool: str,
    args: Any,
    project_dir: Optional[str] = None,
) -> Verdict:
    """Evaluate a candidate call against recent history. Fail-open: ALLOW on error."""
    try:
        cfg = load_config(project_dir)
        candidate = ToolEvent.candidate(session_id, tool, args)
        return evaluate(session_id, cfg, candidate=candidate)
    except Exception:
        return Verdict(ALLOW, "api", "")


def record(
    session_id: str,
    tool: str,
    args: Any,
    ok: bool,
    project_dir: Optional[str] = None,
) -> None:
    """Record the outcome of a completed call. Honors mode=off (inert). Never raises."""
    try:
        cfg = load_config(project_dir)
        if cfg.get("mode") == "off":
            return  # opted-out repo: stay fully inert, record nothing
        append_event(ToolEvent.record(session_id, tool, args, ok))
    except Exception:
        return
