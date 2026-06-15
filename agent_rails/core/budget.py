"""Per-session budget metering.

State: <state_dir>/<session>-budget.json
{
  "tool_calls": int,
  "subagents": int,
  "large_reads": int,
  "approved_tool_calls": int,    # ceiling; raised by approve()
  "subagent_approved": bool
}

Config keys (from cfg dict, typically cfg["budget"]):
  nudge_at:        8    soft warning
  checkpoint_at:   12   blocks until approved; also the initial approved ceiling
  hard_block_at:   20   unconditional block; only reset() clears it
  max_large_reads: 2    advisory nudge when exceeded
  max_subagents:   0    blocks Agent tool unless subagent_approved is set

FAIL-OPEN: all public functions swallow exceptions and return allow/empty on error.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

try:
    import fcntl
    _HAVE_FCNTL = True
except Exception:  # pragma: no cover — Windows
    _HAVE_FCNTL = False

ALLOW = "allow"
NUDGE = "nudge"
BLOCK = "block"

# Tool names Claude Code uses when spawning subagents.
_SUBAGENT_TOOLS = frozenset({"Agent", "Task"})

_DEFAULTS = {
    "nudge_at": 8,
    "checkpoint_at": 12,
    "hard_block_at": 20,
    "max_large_reads": 2,
    "max_subagents": 0,
}


class BudgetVerdict:
    __slots__ = ("action", "reason")

    def __init__(self, action: str, reason: str) -> None:
        self.action = action
        self.reason = reason


# ---------------------------------------------------------------------------
# State-dir helpers (mirrors state.py so both use AGENT_RAILS_STATE_DIR)
# ---------------------------------------------------------------------------

def _state_dir() -> Path:
    base = os.environ.get("AGENT_RAILS_STATE_DIR") or os.path.join(
        tempfile.gettempdir(), "agent-rails"
    )
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _budget_path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return _state_dir() / f"{safe or 'default'}-budget.json"


# ---------------------------------------------------------------------------
# File locking (same pattern as state.py)
# ---------------------------------------------------------------------------

def _lock(fh, exclusive: bool) -> None:
    if not _HAVE_FCNTL:
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
    except Exception:
        pass


def _unlock(fh) -> None:
    if not _HAVE_FCNTL:
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# State serialization
# ---------------------------------------------------------------------------

def _default_state(checkpoint_at: int) -> dict:
    return {
        "tool_calls": 0,
        "subagents": 0,
        "large_reads": 0,
        "approved_tool_calls": checkpoint_at,
        "subagent_approved": False,
    }


def _load_locked(fh, checkpoint_at: int) -> dict:
    state = _default_state(checkpoint_at)
    try:
        fh.seek(0)
        raw = fh.read()
        if not raw.strip():
            return state
        data = json.loads(raw)
        if not isinstance(data, dict):
            return state
        for key in ("tool_calls", "subagents", "large_reads", "approved_tool_calls"):
            v = data.get(key)
            if isinstance(v, int):
                state[key] = v
        v = data.get("subagent_approved")
        if isinstance(v, bool):
            state["subagent_approved"] = v
    except Exception:
        pass
    return state


def _save_locked(fh, state: dict) -> None:
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(state))
        fh.flush()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _cfg_int(cfg: dict, key: str) -> int:
    v = cfg.get(key, _DEFAULTS[key])
    try:
        return max(1, int(v))
    except Exception:
        return _DEFAULTS[key]


def increment_and_check(
    session_id: str,
    tool: str,
    is_large_read: bool,
    cfg: dict,
) -> BudgetVerdict:
    """Atomically increment counters, persist, and return a verdict. Fail-open."""
    try:
        nudge_at = _cfg_int(cfg, "nudge_at")
        checkpoint_at = _cfg_int(cfg, "checkpoint_at")
        hard_block_at = _cfg_int(cfg, "hard_block_at")
        max_large_reads = _cfg_int(cfg, "max_large_reads")
        max_subagents = max(0, int(cfg.get("max_subagents", _DEFAULTS["max_subagents"])))

        path = _budget_path(session_id)
        with path.open("a+", encoding="utf-8") as fh:
            _lock(fh, exclusive=True)
            try:
                state = _load_locked(fh, checkpoint_at)

                state["tool_calls"] += 1
                is_subagent = str(tool).strip() in _SUBAGENT_TOOLS
                if is_subagent:
                    state["subagents"] += 1
                if is_large_read:
                    state["large_reads"] += 1

                tc = state["tool_calls"]
                sa = state["subagents"]
                lr = state["large_reads"]
                approved_tc = state["approved_tool_calls"]
                subagent_approved = state["subagent_approved"]

                _save_locked(fh, state)
            finally:
                _unlock(fh)

        # Hard block: unconditional; only reset() clears it
        if tc > hard_block_at:
            return BudgetVerdict(
                BLOCK,
                f"[agent-rails budget] Hard limit: {tc}/{hard_block_at} tool calls. Reset required.\n\n"
                f"  ! agent-rails budget reset {session_id}",
            )

        # Subagent block
        if is_subagent and sa > max_subagents and not subagent_approved:
            return BudgetVerdict(
                BLOCK,
                f"[agent-rails budget] Subagent blocked: {sa} attempted, {max_subagents} approved.\n\n"
                f"Escalation packet:\n"
                f"- Goal:\n"
                f"- Evidence so far:\n"
                f"- Why subagent (not inline tool calls):\n"
                f"- Bounded scope (exact files or diff range):\n"
                f"- Expected output (diagnosis/verdict, not implementation):\n\n"
                f"Approve:\n"
                f"  ! agent-rails budget approve {session_id} --subagent",
            )

        # Soft checkpoint block
        if tc > approved_tc:
            return BudgetVerdict(
                BLOCK,
                f"[agent-rails budget] Checkpoint: {tc}/{approved_tc} tool calls used.\n\n"
                f"Done:\n"
                f"- \n\n"
                f"Current validation:\n"
                f"- \n\n"
                f"Request:\n"
                f"- +N tools to [specific reason — what the next tool will prove]\n\n"
                f"Approve:\n"
                f"  ! agent-rails budget approve {session_id} --add N",
            )

        # Large-read nudge (over quota but not blocking)
        if is_large_read and lr > max_large_reads:
            return BudgetVerdict(
                NUDGE,
                f"[agent-rails budget] Large unscoped reads: {lr} (soft limit {max_large_reads}). "
                f"Prefer scoped reads (offset+limit).",
            )

        # Tool-call nudge
        if tc > nudge_at:
            return BudgetVerdict(
                NUDGE,
                f"[agent-rails budget] {tc} tool calls used. "
                f"Checkpoint required at {approved_tc + 1}.",
            )

        return BudgetVerdict(ALLOW, "")

    except Exception:
        return BudgetVerdict(ALLOW, "")


def approve(
    session_id: str,
    add_tools: int = 8,
    approve_subagent: bool = False,
) -> dict:
    """Extend the tool-call budget and optionally unblock a subagent.

    Sets approved_tool_calls = max(current_approved, tool_calls + add_tools).
    Returns the updated state dict, or {} on error.
    """
    try:
        path = _budget_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as fh:
            _lock(fh, exclusive=True)
            try:
                state = _load_locked(fh, _DEFAULTS["checkpoint_at"])
                new_ceiling = state["tool_calls"] + max(add_tools, 1)
                state["approved_tool_calls"] = max(state["approved_tool_calls"], new_ceiling)
                if approve_subagent:
                    state["subagent_approved"] = True
                _save_locked(fh, state)
                return dict(state)
            finally:
                _unlock(fh)
    except Exception:
        return {}


def reset(session_id: str) -> bool:
    """Delete the budget state file, fully resetting all counters.

    Returns True if deleted, False if not found, raises nothing.
    """
    try:
        path = _budget_path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False
    except Exception:
        return False


def read_state(session_id: str) -> dict:
    """Return current budget state (read-only). Returns {} on any error."""
    try:
        path = _budget_path(session_id)
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as fh:
            _lock(fh, exclusive=False)
            try:
                raw = fh.read()
            finally:
                _unlock(fh)
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
