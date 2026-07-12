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
                   orientation reads don't burn budget like edits. Adapters may
                   pass command-family tool names for recognized shell calls
                   such as "Bash:agent-rails ledger relevant".
  disable_default_weights:
                   bool — when true, skip built-in weight discounts. Explicit
                   weights still win, then weights["_default"] / default_weight.
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
    # agent-rails ledger bookkeeping. The adapters classify these recognized
    # Bash commands into synthetic tool names so the public budget API stays
    # harness-neutral. Read-only/orientation commands are free; mutations are
    # cheap but nonzero; reverify stays full-price because it can execute an
    # arbitrary falsifier command.
    "Bash:agent-rails ledger check": 0.0,
    "Bash:agent-rails ledger relevant": 0.0,
    "Bash:agent-rails ledger add": 0.2,
    "Bash:agent-rails ledger retire": 0.2,
    "Bash:agent-rails ledger reverify": 1.0,
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
    __slots__ = ("action", "reason", "response")

    def __init__(self, action: str, reason: str, response: str = "observe") -> None:
        self.action = action
        self.reason = reason
        self.response = response


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
        "credit_log": [],
        "last_budget_advisory": "",
        "weighted_at_last_progress": 0.0,
        "last_progress": None,
    }


def _clean_progress_evidence(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "kind", "anchor", "validation_id", "hypothesis_id",
        "state_before", "state_after", "failure_count_before",
        "failure_count_after",
    }
    clean: dict = {}
    for key in allowed:
        item = value.get(key)
        if isinstance(item, str):
            clean[key] = item[:256]
        elif isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            clean[key] = item
    return clean or None


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
        v = data.get("credit_log")
        if isinstance(v, list):
            log = []
            for item in v:
                if (isinstance(item, list) and len(item) == 2
                        and isinstance(item[0], int)
                        and isinstance(item[1], (int, float))
                        and not isinstance(item[1], bool)):
                    log.append([item[0], float(item[1])])
            state["credit_log"] = log
        v = data.get("last_budget_advisory")
        if isinstance(v, str):
            state["last_budget_advisory"] = v
        v = data.get("weighted_at_last_progress")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            state["weighted_at_last_progress"] = max(0.0, float(v))
        v = data.get("last_progress")
        state["last_progress"] = _clean_progress_evidence(v)
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
      2. _DEFAULT_WEIGHTS[tool]          — built-in (unless disabled)
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
        disable_default_weights = (
            isinstance(cfg, dict) and cfg.get("disable_default_weights") is True
        )
        if not disable_default_weights and name in _DEFAULT_WEIGHTS:
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


def _reading_pct(reading, key: str):
    """Read a percent field off a QuotaReading-like object or dict. None if absent
    or not a real number. bool is rejected (isinstance(True, int) is True)."""
    if reading is None:
        return None
    v = getattr(reading, key, None)
    if v is None and isinstance(reading, dict):
        v = reading.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _quota_relief(reading, cfg: dict):
    """Return (window_pct, weekly_pct) when the real quota signal says there is
    plenty of headroom, else None.

    Relief is deliberately conservative: it requires BOTH the rolling-window and
    the weekly used-percent to be known AND below ``quota_relief_below_pct``
    (default 50; set 0 to disable). A missing/null rate-limit (context-only
    reading) yields no relief, so the plain call-count gate stands. Relief only
    ever RELAXES the soft checkpoint — it never touches the hard limit — so a
    stale or wrong reading cannot remove the ultimate backstop.
    """
    try:
        below = cfg.get("quota_relief_below_pct", 50) if isinstance(cfg, dict) else 50
        below = float(below)
        if not (0 < below <= 100):
            return None
        w = _reading_pct(reading, "window_used_pct")
        k = _reading_pct(reading, "weekly_used_pct")
        if w is None or k is None:
            return None
        if w < below and k < below:
            return (w, k)
        return None
    except Exception:
        return None


def _context_fill_threshold(cfg: dict) -> float:
    """Context-fill nudge threshold as a percent, or 0.0 to disable.

    ``context_nudge_pct`` default 80; out-of-range / non-numeric disables the
    nudge (returns 0.0) rather than firing spuriously.
    """
    try:
        v = cfg.get("context_nudge_pct", 80) if isinstance(cfg, dict) else 80
        v = float(v)
        return v if 0 < v <= 100 else 0.0
    except Exception:
        return 0.0


def _quota_scarce(reading, cfg: dict) -> bool:
    """Require a present numeric reading; missing probe data is never danger."""
    try:
        stop = cfg.get("operator_stop", {}) if isinstance(cfg, dict) else {}
        if not isinstance(stop, dict):
            return False
        threshold = float(stop.get("scarcity_used_pct", 85))
        if not (0 < threshold <= 100):
            return False
        return any(
            value is not None and value >= threshold
            for value in (
                _reading_pct(reading, "window_used_pct"),
                _reading_pct(reading, "weekly_used_pct"),
            )
        )
    except Exception:
        return False


def _new_budget_advisory(session_id: str, key: str) -> bool:
    """Return false when this advisory was already emitted for current state."""
    try:
        path = _budget_path(session_id)
        with path.open("a+", encoding="utf-8") as fh:
            _lock(fh, exclusive=True)
            try:
                state = _load_locked(fh, _DEFAULTS["checkpoint_at"])
                if state.get("last_budget_advisory") == key:
                    return False
                state["last_budget_advisory"] = key
                _save_locked(fh, state)
                return True
            finally:
                _unlock(fh)
    except Exception:
        return True


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


def _consume_subagent_approval(session_id: str) -> None:
    """Best-effort one-shot grant consumption for an auto-resumed spawn."""
    try:
        path = _budget_path(session_id)
        with path.open("a+", encoding="utf-8") as fh:
            _lock(fh, exclusive=True)
            try:
                state = _load_locked(fh, _DEFAULTS["checkpoint_at"])
                state["subagent_approved"] = False
                _save_locked(fh, state)
            finally:
                _unlock(fh)
    except Exception:
        return


def increment_and_check(
    session_id: str,
    tool: str,
    is_large_read: bool,
    cfg: dict,
    quota_reading=None,
    mechanical_signal: bool = False,
) -> BudgetVerdict:
    """Atomically increment counters, persist, and return a verdict. Fail-open.

    The weighted_calls counter is what budget gates (checkpoint + hard limit)
    compare against. tool_calls remains the raw count and is unaffected by
    per-tool weights — it keeps tripwire history aligned and provides a
    stable "calls so far" number for messages.

    ``quota_reading`` is an optional harness-supplied snapshot of the *real*
    subscription quota (see ``adapters/codex/quota.QuotaReading``): a
    QuotaReading-like object or dict exposing ``window_used_pct`` /
    ``weekly_used_pct``. When present and both are comfortably low, the SOFT
    checkpoint is deferred — the call counter is only a proxy, and the real
    signal says there is headroom. It never defers the hard limit and never
    blocks; a None/absent/stale reading falls back to the pure call-count gate,
    so this is a fail-open relaxation only.
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

                # A grant authorizes exactly one spawn beyond the monotonic
                # allowance. Consume it atomically with the attempted spawn.
                if is_subagent and sa > max_subagents and subagent_approved:
                    state["subagent_approved"] = False
                    subagent_approved = True

                _save_locked(fh, state)
            finally:
                _unlock(fh)

        poll_timeout_s = _cfg_int(cfg, "poll_timeout_s")

        type_tag = f", type: {task_type}" if task_type else ""

        # Operator stop: volume alone never denies. The default stop also
        # requires a recent strong mechanical signal or fresh measured scarcity.
        has_stop_cfg = isinstance(cfg.get("operator_stop"), dict)
        stop_cfg = cfg.get("operator_stop", {})
        if not isinstance(stop_cfg, dict):
            stop_cfg = {}
        stop_enabled = bool(stop_cfg.get("enabled", True))
        unconditional = bool(stop_cfg.get("unconditional", not has_stop_cfg))
        try:
            stall_window = max(1.0, float(stop_cfg.get(
                "stall_window_weighted", 30 if has_stop_cfg else 1
            )))
        except (TypeError, ValueError):
            stall_window = 30.0
        since_progress = wc - float(state.get("weighted_at_last_progress", 0.0))
        positive_danger = bool(mechanical_signal) or _quota_scarce(quota_reading, cfg)
        if (stop_enabled and wc > hard_block_at and wc > approved_tc
                and since_progress >= stall_window and (unconditional or positive_danger)):
            hard_msg = (
                f"[agent-rails budget] Hard limit: {_fmt_calls(wc, tc)}/{hard_block_at} weighted calls{type_tag}.\n\n"
                f"Extend the budget:\n"
                f"  ! agent-rails budget {session_id} add N\n\n"
                f"Or reset (clears all session state):\n"
                f"  ! agent-rails budget {session_id} reset"
            )
            print(f"\n{hard_msg}\n", file=sys.stderr, flush=True)
            if _poll_for_approval(session_id, poll_timeout_s, wc, reset_only=False):
                return BudgetVerdict(ALLOW, "")
            return BudgetVerdict(BLOCK, hard_msg, "operator_stop")

        # Subagent block
        if is_subagent and sa > max_subagents and not subagent_approved:
            subagent_msg = (
                f"[agent-rails budget] Subagent blocked: {sa} attempted, {max_subagents} approved.\n"
                f"Approve to continue (Claude will resume automatically):\n"
                f"  ! agent-rails budget {session_id} subagent"
            )
            print(f"\n{subagent_msg}\n", file=sys.stderr, flush=True)
            if _poll_for_subagent_approval(session_id, poll_timeout_s):
                _consume_subagent_approval(session_id)
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
                f"  ! agent-rails budget {session_id} subagent",
            )

        # Soft checkpoint. Healthy quota suppresses the proxy advisory. By
        # default the checkpoint is advisory; trusted config can opt into denial.
        if wc > approved_tc:
            relief = _quota_relief(quota_reading, cfg)
            if relief is not None:
                if "checkpoint_deny" in cfg:
                    return BudgetVerdict(ALLOW, "")
                w_pct, k_pct = relief  # legacy direct-call response
                return BudgetVerdict(
                    NUDGE,
                    f"[agent-rails budget] Checkpoint deferred at {_fmt_calls(wc, tc)} weighted "
                    f"calls{type_tag}: real quota is low (window {w_pct:.0f}%, weekly {k_pct:.0f}%). "
                    f"Hard limit at {hard_block_at} still applies.",
                )
            checkpoint_denies = bool(cfg.get("checkpoint_deny", True))
            if not checkpoint_denies:
                advisory_key = f"checkpoint:{approved_tc}:{task_type or ''}"
                if not _new_budget_advisory(session_id, advisory_key):
                    return BudgetVerdict(ALLOW, "")
                return BudgetVerdict(
                    NUDGE,
                    f"[agent-rails budget] Checkpoint advised: {_fmt_calls(wc, tc)}/{approved_tc} "
                    f"weighted calls used{type_tag}. Review progress before continuing; "
                    f"approval is optional unless trusted operator config enables denial.",
                    "checkpoint",
                )
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
                    f"  agent-rails budget {session_id} add N --self\n"
                    f"  (N = 1-{sa_max_add}; {sa_remaining}/{sa_max_times} uses remaining)\n\n"
                    f"Human approval — fallback when self-approve isn't appropriate "
                    f"(open-ended work, exhausted slots):\n"
                    f"  ! agent-rails budget {session_id} add N",
                    "checkpoint",
                )

            # Self-approve unavailable: poll for human approval, then deny.
            checkpoint_msg = (
                f"[agent-rails budget] Checkpoint: {_fmt_calls(wc, tc)}/{approved_tc} weighted calls used{type_tag}.\n"
                f"Approve to continue (Claude will resume automatically):\n"
                f"  ! agent-rails budget {session_id} add N"
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
                f"  ! agent-rails budget {session_id} add N",
                "checkpoint",
            )

        # Context-fill advisory (nudge only). A nearly-full context window is
        # re-sent every turn — a real CLI cost even when the call count is low —
        # so this is checked ahead of the call-count nudges. Populated by the
        # Claude transcript probe and the Codex rollout alike.
        ctx_pct = _reading_pct(quota_reading, "context_used_pct")
        ctx_thresh = _context_fill_threshold(cfg)
        if ctx_pct is not None and ctx_thresh > 0 and ctx_pct >= ctx_thresh:
            return BudgetVerdict(
                NUDGE,
                f"[agent-rails budget] Context ~{ctx_pct:.0f}% full{type_tag}. A full "
                f"context is re-sent every turn — wrap up, /compact, or start a fresh "
                f"session before it degrades and gets expensive.",
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
        if not path.exists():
            return {
                "ok": False,
                "reason": f"no budget state found for session: {session_id}",
                "state": {},
            }
        with path.open("r+", encoding="utf-8") as fh:
            _lock(fh, exclusive=True)
            try:
                fh.seek(0)
                raw = fh.read()
                if not raw.strip():
                    return {
                        "ok": False,
                        "reason": f"no budget state found for session: {session_id}",
                        "state": {},
                    }
                try:
                    data = json.loads(raw)
                except Exception:
                    data = None
                if not isinstance(data, dict):
                    return {
                        "ok": False,
                        "reason": f"malformed budget state for session: {session_id}; use human approval",
                        "state": {},
                    }
                fh.seek(0)
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


def credit_progress(
    session_id: str,
    credit: float,
    max_per_window: float = 0.0,
    window: int = 0,
    evidence: dict | None = None,
) -> dict:
    """Relieve budget pressure by an observed-progress credit.

    Decrements the live ``weighted_calls`` counter (clamped at 0), which is what
    both the checkpoint and hard-limit gates compare against. This is the
    positive counterpart to ``increment_and_check``: spend accrues per call,
    observed progress pays it back, so a converging session keeps earning
    headroom while a stalled one re-checkpoints.

    ``tool_calls`` (the raw count) is deliberately NOT touched — it anchors the
    approval ceiling (``approve`` sets it relative to ``tool_calls``) and the
    detector history, so productive work keeps its ceiling rising while the live
    counter falls. Credit only ever LOWERS pressure; it can never block a call.

    Cap: when ``max_per_window > 0`` and ``window > 0``, the total credit applied
    within the trailing ``window`` tool calls is capped at ``max_per_window``.
    This bounds how far progress can defer oversight, so an agent cannot
    indefinitely farm cheap validations to outrun every checkpoint — within any
    window, at most one cap's worth of relief. The per-credit ledger is pruned
    to the window on each call so it stays bounded.

    Fail-open: returns {} on any error or when there is no state to credit.
    """
    try:
        c = float(credit)
        if c <= 0:
            return {}
        path = _budget_path(session_id)
        if not path.exists():
            return {}  # nothing spent yet — nothing to credit
        with path.open("a+", encoding="utf-8") as fh:
            _lock(fh, exclusive=True)
            try:
                state = _load_locked(fh, _DEFAULTS["checkpoint_at"])
                tc = int(state.get("tool_calls", 0))
                effective = c
                if max_per_window > 0 and window > 0:
                    cutoff = tc - int(window)
                    log = [
                        e for e in state.get("credit_log", [])
                        if isinstance(e, list) and len(e) == 2 and e[0] > cutoff
                    ]
                    used = sum(float(e[1]) for e in log)
                    allowable = max(0.0, float(max_per_window) - used)
                    effective = min(c, allowable)
                    if effective > 0:
                        log.append([tc, effective])
                    # Defensive bound: pruning by window normally keeps this
                    # tiny, but a misconfigured window larger than the session
                    # would never prune. Cap length so state can't bloat.
                    if len(log) > 256:
                        log = log[-256:]
                    state["credit_log"] = log
                if effective <= 0:
                    # Cap exhausted for this window: persist the pruned ledger
                    # but apply no relief.
                    _save_locked(fh, state)
                    return dict(state)
                state["weighted_calls"] = max(
                    0.0, float(state.get("weighted_calls", 0.0)) - effective
                )
                state["weighted_at_last_progress"] = state["weighted_calls"]
                state["last_budget_advisory"] = ""
                if isinstance(evidence, dict):
                    state["last_progress"] = _clean_progress_evidence(evidence)
                _save_locked(fh, state)
                return dict(state)
            finally:
                _unlock(fh)
    except Exception:
        return {}


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
        if not isinstance(data, dict):
            return {}
        if "last_progress" in data:
            data["last_progress"] = _clean_progress_evidence(data.get("last_progress"))
        return data
    except Exception:
        return {}
