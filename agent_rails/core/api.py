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

from .audit import log_verdict
from .engine import evaluate
from .events import ToolEvent
from .state import append_event
from ..config import load_config
from ..detectors.base import ALLOW, BLOCK, Verdict


def check(
    session_id: str,
    tool: str,
    args: Any,
    project_dir: Optional[str] = None,
) -> Verdict:
    """Evaluate a candidate call against recent history. Fail-open: ALLOW on error.

    Side effect: when this returns an ENFORCED block, it records a BLOCKED
    marker for the denied call. A denied call never runs, so no PostToolUse
    follows it; without the marker the error_streak detector — which is
    candidate-independent — would keep denying every subsequent call and wedge
    the agent. Recording the intervention lets the streak reset so the agent
    can run the diagnostic the block demands. Recorded HERE, in the one shared
    site, so no adapter can reintroduce the wedge by forgetting it.
    """
    try:
        cfg = load_config(project_dir)
        candidate = ToolEvent.candidate(session_id, tool, args)
        verdict = evaluate(session_id, cfg, candidate=candidate)
        log_verdict(session_id, tool, verdict)  # observability; no-op on ALLOW
        if verdict.action == BLOCK:  # real, enforced block (observe downgrades to nudge)
            try:
                append_event(ToolEvent.blocked(session_id, tool, args))
            except Exception:
                pass  # recording is best-effort; never turn a block into a crash
        return verdict
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
