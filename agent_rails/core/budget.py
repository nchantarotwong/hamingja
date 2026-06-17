"""Per-session budget metering.

State: <state_dir>/<session>-budget.json
{
  "tool_calls": int,             # raw count (1 per call) — tripwire history reference
  "weighted_calls": float,       # weight-adjusted count — what the budget gate compares against
  "subagents": int,
  "large_reads": int,
  "approved_tool_calls": int,    # ceiling; raised by approve()
  "subagent_approved": bool,
  "self_approve_times": int,
  "task_type": str | null        # declared bucket; picks per-type thresholds when set
}

Config keys (from cfg dict, typically cfg["budget"]):
  nudge_at:        8    soft warning
  checkpoint_at:   25   blocks until approved; also the initial approved ceiling
  hard_block_at:   60   hard block; approve() or reset() clears it
  max_large_reads: 2    advisory nudge when exceeded
  max_subagents:   0    blocks Agent tool unless subagent_approved is set
  poll_timeout_s:  60   seconds the hook waits for approval before denying
  weights:         dict {tool_name: float} — per-tool cost multipliers for the
                   weighted_calls counter. Missing tools fall back to
                   weights["_default"] then to 1.0. Built-in defaults give
                   read-class tools (Read/Glob/Grep/LS/etc.) a 0.5 cost so
                   orientation reads don't burn budget like edits.
  task_types:      dict {type_name: {checkpoint_at, hard_block_at}} — per-bucket
                   threshold overrides. When state["task_type"] is set, the
                   matching bucket's thresholds override the top-level ones.
                   Built-in taxonomy: trivial/standard/debug/audit/explore.

The weighted_calls counter is what the budget gate compares against. tool_calls
is preserved unchanged so the detector history (repetition/oscillation/error_streak)
stays consistent across versions and so back-compat math works.

FAIL-OPEN: all public functions swallow exceptions and return allow/empty on error.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
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
    "checkpoint_at": 25,
    "hard_block_at": 60,
    "max_large_reads": 2,
    "max_subagents": 0,
    "poll_timeout_s": 60,
    # self_approve.replenish_every: 0 disables; otherwise one self-approve
    # slot is replenished per N tool calls past checkpoint_at.
    "replenish_every": 20,
    # Default per-tool weight when neither cfg["weights"][tool] nor
    # cfg["weights"]["_default"] is set.
    "default_weight": 1.0,
}

# Built-in per-tool cost weights. Read-class tools cost less so orientation
# reads don't trigger a premature checkpoint. Mutating tools and slow/external
# fetches stay at 1.0. Mergeable / overridable via cfg["weights"].
_DEFAULT_WEIGHTS: dict[str, float] = {
    # Cheap read-class: file system + history queries
    "Read": 0.5,
    "Glob": 0.5,
    "Grep": 0.5,
    "LS": 0.5,
    "NotebookRead": 0.5,
    "TodoRead": 0.5,
    "TaskList": 0.5,
    "TaskGet": 0.5,
    # Slightly cheaper: external fetches (slower & often unbounded, so keep
    # some cost on them).
    "WebFetch": 0.75,
    "WebSearch": 0.75,
}

# Built-in per-bucket budget overrides. None of these apply unless the agent
# explicitly sets state["task_type"]; default behaviour matches today's globals.
# Calibrated from real-world friction: 13/20 was too tight for anything but
# trivial work; debug/audit sessions routinely use 30-50 calls before a fix
# lands.
_DEFAULT_TASK_TYPES: dict[str, dict[str, int]] = {
    "trivial":  {"checkpoint_at": 10, "hard_block_at": 25},
    "standard": {"checkpoint_at": 25, "hard_block_at": 60},
    "debug":    {"checkpoint_at": 35, "hard_block_at": 80},
    "audit":    {"checkpoint_at": 50, "hard_block_at": 120},
    "explore":  {"checkpoint_at": 75, "hard_block_at": 150},
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
        "weighted_calls": 0.0,
        "subagents": 0,
        "large_reads": 0,
        "approved_tool_calls": checkpoint_at,
        "subagent_approved": False,
        "self_approve_times": 0,
        "task_type": None,
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
        v = data.get("weighted_calls")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            state["weighted_calls"] = max(0.0, float(v))
        else:
            # Back-compat: old state files lack weighted_calls. Seed it from
            # tool_calls so the first new tool call resumes counting cleanly.
            state["weighted_calls"] = float(state["tool_calls"])
        v = data.get("subagent_approved")
        if isinstance(v, bool):
            state["subagent_approved"] = v
        v = data.get("self_approve_times")
        if isinstance(v, int):
            state["self_approve_times"] = max(0, v)
        v = data.get("task_type")
        if isinstance(v, str) and v.strip():
            state["task_type"] = v.strip()
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
    v = cfg.get(key, _DEFAULTS.get(key, 1))
    try:
        floor = 0 if key in ("poll_timeout_s", "replenish_every") else 1
        return max(floor, int(v))
    except Exception:
        return _DEFAULTS.get(key, 1)


def _tool_weight(tool: str, cfg: dict) -> float:
    """Resolve the cost weight for a given tool name.

    Resolution order (first match wins):
      1. cfg["weights"][tool]            — explicit per-tool override
      2. _DEFAULT_WEIGHTS[tool]          — built-in (read-class tools = 0.5 etc.)
      3. cfg["weights"]["_default"]      — explicit catch-all override
      4. cfg["default_weight"]           — explicit default
      5. _DEFAULTS["default_weight"]     — 1.0

    Clamps to [0.0, 10.0] to keep a single malformed config from blowing the
    budget. Fail-open: any unexpected error returns 1.0.
    """
    try:
        name = str(tool).strip()
        weights = cfg.get("weights") if isinstance(cfg, dict) else None
        if isinstance(weights, dict):
            if name in weights:
                return _clamp_weight(weights[name])
        if name in _DEFAULT_WEIGHTS:
            built_in = _DEFAULT_WEIGHTS[name]
            # An operator's explicit cfg["weights"][name] would have won above;
            # but cfg["weights"]["_default"] should NOT override a built-in
            # tool-specific entry, so we return the built-in here.
            return built_in
        if isinstance(weights, dict) and "_default" in weights:
            return _clamp_weight(weights["_default"])
        if isinstance(cfg, dict) and "default_weight" in cfg:
            return _clamp_weight(cfg["default_weight"])
        return float(_DEFAULTS["default_weight"])
    except Exception:
        return 1.0


def _clamp_weight(v) -> float:
    try:
        return max(0.0, min(10.0, float(v)))
    except (TypeError, ValueError):
        return 1.0


def _resolve_task_type_cfg(cfg: dict, type_name: str | None) -> dict:
    """Return the threshold dict for a task type, or {} if none applies.

    Resolution: cfg["task_types"][name] overrides _DEFAULT_TASK_TYPES[name].
    Only the keys actually present in the merged dict are returned; callers
    fall back to top-level cfg values for anything missing.
    """
    if not type_name:
        return {}
    name = str(type_name).strip()
    if not name:
        return {}
    out: dict[str, int] = {}
    built_in = _DEFAULT_TASK_TYPES.get(name)
    if isinstance(built_in, dict):
        out.update(built_in)
    user_types = cfg.get("task_types") if isinstance(cfg, dict) else None
    if isinstance(user_types, dict):
        user = user_types.get(name)
        if isinstance(user, dict):
            for k in ("checkpoint_at", "hard_block_at"):
                if k in user:
                    try:
                        out[k] = max(1, int(user[k]))
                    except (TypeError, ValueError):
                        pass
    return out


def _effective_thresholds(cfg: dict, task_type: str | None) -> tuple[int, int]:
    """Return (checkpoint_at, hard_block_at), honoring task_type when set.

    Falls back to top-level cfg values when the type has no override or no
    type is set.
    """
    base_cp = _cfg_int(cfg, "checkpoint_at")
    base_hb = _cfg_int(cfg, "hard_block_at")
    type_cfg = _resolve_task_type_cfg(cfg, task_type)
    cp = max(1, int(type_cfg.get("checkpoint_at", base_cp)))
    hb = max(1, int(type_cfg.get("hard_block_at", base_hb)))
    # Ensure the hard block is reachable past the checkpoint. If a user
    # configures hb < cp, raise hb to cp so we don't fail-open by accident.
    if hb < cp:
        hb = cp
    return cp, hb


def known_task_types(cfg: dict) -> list[str]:
    """Return the sorted union of built-in + user-defined task type names."""
    names = set(_DEFAULT_TASK_TYPES.keys())
    user = cfg.get("task_types") if isinstance(cfg, dict) else None
    if isinstance(user, dict):
        for k, v in user.items():
            if isinstance(k, str) and k.strip() and isinstance(v, dict):
                names.add(k.strip())
    return sorted(names)


def _sa_cfg(sub: object) -> tuple[bool, int, int, int]:
    """Pull (enabled, max_add, max_times, replenish_every) from the self_approve sub-dict.

    Callers pass the SUB-DICT directly (not the wrapping budget cfg), so this
    can't accidentally interpret the budget's ``"enabled": True`` as self-approve
    being enabled. Returns disabled defaults for None / non-dict input.
    """
    sa = sub if isinstance(sub, dict) else {}
    enabled = bool(sa.get("enabled", False))
    try:
        max_add = max(1, int(sa.get("max_add", 3)))
    except Exception:
        max_add = 3
    try:
        max_times = max(0, int(sa.get("max_times_per_session", 2)))
    except Exception:
        max_times = 2
    try:
        # replenish_every floor of 0 = disabled
        replenish_every = max(0, int(sa.get("replenish_every", _DEFAULTS["replenish_every"])))
    except Exception:
        replenish_every = _DEFAULTS["replenish_every"]
    return enabled, max_add, max_times, replenish_every


def _sa_remaining(
    tool_calls: int,
    self_approve_times: int,
    checkpoint_at: int,
    max_times: int,
    replenish_every: int,
) -> int:
    """Return remaining self-approve slots given replenishment.

    Replenishment: one slot is restored per ``replenish_every`` tool calls past
    ``checkpoint_at``. ``replenish_every <= 0`` disables replenishment.
    """
    if max_times <= 0:
        return 0
    if replenish_every > 0 and tool_calls > checkpoint_at:
        # Cast to int — `tool_calls` may now be a float (weighted counter),
        # and we don't want fractional `replenished` propagating into the
        # f-string ("1.0/2 uses remaining" looks broken).
        replenished = int((tool_calls - checkpoint_at) // replenish_every)
    else:
        replenished = 0
    effective_used = max(0, self_approve_times - replenished)
    return max(0, max_times - effective_used)


def _fmt_calls(weighted: float, raw: int) -> str:
    """Format weighted/raw for messages. Shows just one number if they match
    (i.e. no weights are configured), else "weighted (raw N)"."""
    # Treat near-equal floats as equal to avoid noisy "(raw N)" when all
    # weights are 1.0 but rounding nudged things off by epsilon.
    if abs(weighted - raw) < 0.05:
        return f"{int(round(weighted))}"
    # Trim trailing zero on .0 / .5 weights for readability.
    if abs(weighted - round(weighted)) < 0.01:
        return f"{int(round(weighted))} (raw {raw})"
    return f"{weighted:.1f} (raw {raw})"


def _poll_for_approval(
    session_id: str, timeout_s: int, blocked_at_tc: float, reset_only: bool = False
) -> bool:
    """Poll the budget state file until the block clears or timeout.

    reset_only=True: only clears on reset() (file deletion).
    reset_only=False: clears on approve() (ceiling raised) or reset().
    Returns True if cleared, False on timeout. Fails open on read errors.
    """
    if timeout_s <= 0:
        return False
    path = _budget_path(session_id)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(0.5)
        try:
            if not path.exists():
                return True  # reset() cleared state
            if not reset_only:
                with path.open("r", encoding="utf-8") as fh:
                    _lock(fh, exclusive=False)
                    try:
                        raw = fh.read()
                    finally:
                        _unlock(fh)
                data = json.loads(raw) if raw.strip() else {}
                if isinstance(data, dict):
                    if blocked_at_tc <= data.get("approved_tool_calls", 0):
                        return True
        except Exception:
            pass
    return False


def _poll_for_subagent_approval(session_id: str, timeout_s: int) -> bool:
    """Poll until subagent_approved is set or state is gone. Returns True if cleared."""
    if timeout_s <= 0:
        return False
    path = _budget_path(session_id)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(0.5)
        try:
            if not path.exists():
                return True  # reset() cleared state
            with path.open("r", encoding="utf-8") as fh:
                _lock(fh, exclusive=False)
                try:
                    raw = fh.read()
                finally:
                    _unlock(fh)
            data = json.loads(raw) if raw.strip() else {}
            if isinstance(data, dict) and data.get("subagent_approved"):
                return True
        except Exception:
            pass
    return False


def increment_and_check(
    session_id: str,
    tool: str,
    is_large_read: bool,
    cfg: dict,
) -> BudgetVerdict:
    """Atomically increment counters, persist, and return a verdict. Fail-open.

    The weighted_calls counter is what budget gates (checkpoint + hard limit)
    compare against. tool_calls remains the raw count and is unaffected by
    per-tool weights — it keeps tripwire history aligned and provides a
    stable "calls so far" number for messages.
    """
    try:
        nudge_at = _cfg_int(cfg, "nudge_at")
        max_large_reads = _cfg_int(cfg, "max_large_reads")
        max_subagents = max(0, int(cfg.get("max_subagents", _DEFAULTS["max_subagents"])))

        weight = _tool_weight(tool, cfg)

        path = _budget_path(session_id)
        with path.open("a+", encoding="utf-8") as fh:
            _lock(fh, exclusive=True)
            try:
                # Seed with the global checkpoint; the per-type override (if any)
                # is applied below, after we know state["task_type"].
                global_checkpoint_at = _cfg_int(cfg, "checkpoint_at")
                state = _load_locked(fh, global_checkpoint_at)

                state["tool_calls"] += 1
                state["weighted_calls"] = float(state.get("weighted_calls", 0.0)) + weight
                is_subagent = str(tool).strip() in _SUBAGENT_TOOLS
                if is_subagent:
                    state["subagents"] += 1
                if is_large_read:
                    state["large_reads"] += 1

                tc = state["tool_calls"]
                wc = state["weighted_calls"]
                sa = state["subagents"]
                lr = state["large_reads"]
                approved_tc = state["approved_tool_calls"]
                subagent_approved = state["subagent_approved"]
                sa_times_used = state["self_approve_times"]
                task_type = state.get("task_type")

                checkpoint_at, hard_block_at = _effective_thresholds(cfg, task_type)

                _save_locked(fh, state)
            finally:
                _unlock(fh)

        poll_timeout_s = _cfg_int(cfg, "poll_timeout_s")

        type_tag = f", type: {task_type}" if task_type else ""

        # Hard block: approve() or reset() clears it
        if wc > hard_block_at and wc > approved_tc:
            hard_msg = (
                f"[agent-rails budget] Hard limit: {_fmt_calls(wc, tc)}/{hard_block_at} weighted calls{type_tag}.\n\n"
                f"Extend the budget:\n"
                f"  ! agent-rails budget approve {session_id} --add N\n\n"
                f"Or reset (clears all session state):\n"
                f"  ! agent-rails budget reset {session_id}"
            )
            print(f"\n{hard_msg}\n", file=sys.stderr, flush=True)
            if _poll_for_approval(session_id, poll_timeout_s, wc, reset_only=False):
                return BudgetVerdict(ALLOW, "")
            return BudgetVerdict(BLOCK, hard_msg)

        # Subagent block
        if is_subagent and sa > max_subagents and not subagent_approved:
            subagent_msg = (
                f"[agent-rails budget] Subagent blocked: {sa} attempted, {max_subagents} approved.\n"
                f"Approve to continue (Claude will resume automatically):\n"
                f"  ! agent-rails budget approve {session_id} --subagent"
            )
            print(f"\n{subagent_msg}\n", file=sys.stderr, flush=True)
            if _poll_for_subagent_approval(session_id, poll_timeout_s):
                return BudgetVerdict(ALLOW, "")
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
        if wc > approved_tc:
            sa_enabled, sa_max_add, sa_max_times, sa_replenish = _sa_cfg(
                cfg.get("self_approve")
            )
            sa_remaining = _sa_remaining(
                wc, sa_times_used, checkpoint_at, sa_max_times, sa_replenish
            )

            # When self-approve is available, skip the 60s polling: the agent
            # can self-approve inline (Bash exemption in tripwire) and retry in
            # milliseconds. Polling is reserved for the human-approval path.
            if sa_enabled and sa_remaining > 0:
                return BudgetVerdict(
                    BLOCK,
                    f"[agent-rails budget] Checkpoint: {_fmt_calls(wc, tc)}/{approved_tc} weighted calls used{type_tag}.\n\n"
                    f"Self-approve to continue. Run this as a Bash tool call, then retry:\n"
                    f"  agent-rails budget approve {session_id} --self --add N\n"
                    f"  (N = 1-{sa_max_add}; {sa_remaining}/{sa_max_times} uses remaining)\n\n"
                    f"Human approval — fallback when self-approve isn't appropriate "
                    f"(open-ended work, exhausted slots):\n"
                    f"  ! agent-rails budget approve {session_id} --add N",
                )

            # Self-approve unavailable: poll for human approval, then deny.
            checkpoint_msg = (
                f"[agent-rails budget] Checkpoint: {_fmt_calls(wc, tc)}/{approved_tc} weighted calls used{type_tag}.\n"
                f"Approve to continue (Claude will resume automatically):\n"
                f"  ! agent-rails budget approve {session_id} --add N"
            )
            print(f"\n{checkpoint_msg}\n", file=sys.stderr, flush=True)
            if _poll_for_approval(session_id, poll_timeout_s, wc):
                return BudgetVerdict(ALLOW, "")

            return BudgetVerdict(
                BLOCK,
                f"[agent-rails budget] Checkpoint: {_fmt_calls(wc, tc)}/{approved_tc} weighted calls used{type_tag}.\n\n"
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
        if wc > nudge_at:
            return BudgetVerdict(
                NUDGE,
                f"[agent-rails budget] {_fmt_calls(wc, tc)} weighted calls used{type_tag}. "
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


def self_approve(session_id: str, add_tools: int, cfg: dict) -> dict:
    """Attempt an agent-initiated self-approve: validate limits then extend budget.

    cfg is the ``budget.self_approve`` sub-dict from the resolved config.
    Returns {"ok": bool, "reason": str, "state": dict}.  Fail-open on error.
    """
    try:
        # Public API accepts either the inner self_approve sub-dict (the common
        # case — CLI passes this through) or the wrapping budget cfg.
        if isinstance(cfg, dict) and isinstance(cfg.get("self_approve"), dict):
            sub = cfg.get("self_approve")
        else:
            sub = cfg
        enabled, max_add, max_times, replenish_every = _sa_cfg(sub)
        if not enabled:
            return {"ok": False, "reason": "self-approve is disabled in config", "state": {}}

        if add_tools > max_add:
            return {
                "ok": False,
                "reason": (
                    f"--add {add_tools} exceeds self-approve max_add={max_add}; "
                    f"use human approval"
                ),
                "state": {},
            }

        # checkpoint_at is needed for replenishment math; pull from the parent cfg
        # if caller passed the wrapping budget dict, else fall back to the default.
        checkpoint_at = _DEFAULTS["checkpoint_at"]
        if isinstance(cfg, dict) and "checkpoint_at" in cfg:
            try:
                checkpoint_at = max(1, int(cfg["checkpoint_at"]))
            except Exception:
                pass

        path = _budget_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as fh:
            _lock(fh, exclusive=True)
            try:
                state = _load_locked(fh, checkpoint_at)
                times_used = state.get("self_approve_times", 0)
                remaining = _sa_remaining(
                    state["tool_calls"], times_used, checkpoint_at, max_times, replenish_every
                )
                if remaining <= 0:
                    return {
                        "ok": False,
                        "reason": (
                            f"self-approve exhausted ({times_used}/{max_times} uses); "
                            f"use human approval"
                        ),
                        "state": dict(state),
                    }
                new_ceiling = state["tool_calls"] + max(add_tools, 1)
                state["approved_tool_calls"] = max(state["approved_tool_calls"], new_ceiling)
                state["self_approve_times"] = times_used + 1
                _save_locked(fh, state)
                return {"ok": True, "reason": "", "state": dict(state)}
            finally:
                _unlock(fh)
    except Exception:
        return {"ok": False, "reason": "unexpected error; use human approval", "state": {}}


def set_task_type(session_id: str, type_name: str, cfg: dict) -> dict:
    """Declare the task type for a session.

    Validates that ``type_name`` is one of the known types (built-in or
    project-defined). If the new type has a higher checkpoint_at than the
    current ``approved_tool_calls``, the ceiling is raised so the agent gets
    the bucket's full allowance immediately rather than tripping a checkpoint
    on the next call.

    Returns ``{"ok": bool, "reason": str, "state": dict}``. Fail-open on any
    unexpected error so a misconfiguration can't brick a session.
    """
    try:
        name = (type_name or "").strip()
        if not name:
            return {"ok": False, "reason": "task type cannot be empty", "state": {}}

        valid = known_task_types(cfg)
        if name not in valid:
            return {
                "ok": False,
                "reason": (
                    f"unknown task type: {name!r}; "
                    f"known types: {', '.join(valid)}"
                ),
                "state": {},
            }

        type_cfg = _resolve_task_type_cfg(cfg, name)
        type_checkpoint = int(type_cfg.get("checkpoint_at", _cfg_int(cfg, "checkpoint_at")))

        path = _budget_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as fh:
            _lock(fh, exclusive=True)
            try:
                state = _load_locked(fh, _cfg_int(cfg, "checkpoint_at"))
                state["task_type"] = name
                # Raise the ceiling immediately so the new bucket's allowance
                # is felt on the next call (we never *lower* an existing
                # ceiling — that would surprise the agent mid-task).
                state["approved_tool_calls"] = max(
                    state["approved_tool_calls"], type_checkpoint
                )
                _save_locked(fh, state)
                return {"ok": True, "reason": "", "state": dict(state)}
            finally:
                _unlock(fh)
    except Exception:
        return {"ok": False, "reason": "unexpected error; check config", "state": {}}


def get_task_type(session_id: str) -> str | None:
    """Return the declared task type for a session, or None."""
    state = read_state(session_id)
    v = state.get("task_type") if isinstance(state, dict) else None
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def reset(session_id: str, add_tools: int = 0) -> bool:
    """Delete the budget state file, fully resetting all counters.

    If add_tools > 0, immediately writes a fresh state with
    approved_tool_calls = checkpoint_at + add_tools so the resumed
    session has a head-start before the next checkpoint fires.

    Returns True if a file was deleted, False if not found, raises nothing.
    """
    try:
        path = _budget_path(session_id)
        deleted = False
        if path.exists():
            path.unlink()
            deleted = True
        if add_tools > 0:
            fresh = _default_state(_DEFAULTS["checkpoint_at"])
            fresh["approved_tool_calls"] = _DEFAULTS["checkpoint_at"] + max(0, add_tools)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(fresh))
        return deleted
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
