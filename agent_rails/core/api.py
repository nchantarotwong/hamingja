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
from .budget import credit_progress
from .engine import evaluate
from .events import ToolEvent
from .progress import assess_progress
from .state import append_event, read_recent
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
    output: Any = None,
) -> None:
    """Record the outcome of a completed call. Honors mode=off (inert). Never raises."""
    try:
        cfg = load_config(project_dir)
        if cfg.get("mode") == "off":
            return  # opted-out repo: stay fully inert, record nothing
        append_event(ToolEvent.record(session_id, tool, args, ok, output=output))
        _credit_observed_progress(session_id, cfg)
    except Exception:
        return


def _credit_observed_progress(session_id: str, cfg: dict) -> None:
    """Assess observed progress from the just-recorded event and relieve budget.

    Best-effort and fully isolated: any failure here must never affect
    recording, and a credit can only ever LOWER budget pressure, never raise it.
    Skipped when the budget is disabled (nothing to credit against).
    """
    try:
        budget_cfg = cfg.get("budget") if isinstance(cfg, dict) else None
        if not isinstance(budget_cfg, dict) or not budget_cfg.get("enabled", True):
            return
        try:
            window = int(cfg.get("window", 12))
        except (TypeError, ValueError):
            window = 12
        events = read_recent(session_id, window if window > 0 else 12)
        signal = assess_progress(events, budget_cfg)
        if signal is not None and signal.credit > 0:
            credit_progress(session_id, signal.credit)
    except Exception:
        return
