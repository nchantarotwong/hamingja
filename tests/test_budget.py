"""Tests for agent_rails.core.budget."""
from __future__ import annotations

import json
import os
import pytest

from agent_rails.core.budget import (
    ALLOW,
    BLOCK,
    NUDGE,
    _budget_path,
    _default_state,
    approve,
    increment_and_check,
    read_state,
    reset,
    self_approve,
)

SESSION = "test-budget-session"

_CFG = {
    "enabled": True,
    "nudge_at": 8,
    "checkpoint_at": 12,
    "hard_block_at": 20,
    "max_large_reads": 2,
    "max_subagents": 0,
    "poll_timeout_s": 0,  # disable polling in tests; production config sets this to ~60
}


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    yield
    # budget_path recomputed each call so no extra teardown needed


# ---------------------------------------------------------------------------
# Basic allow / nudge / block thresholds
# ---------------------------------------------------------------------------

def test_allow_under_nudge():
    for _ in range(8):
        bv = increment_and_check(SESSION, "Bash", False, _CFG)
    assert bv.action == ALLOW


def test_nudge_at_nudge_threshold():
    for _ in range(9):
        bv = increment_and_check(SESSION, "Bash", False, _CFG)
    assert bv.action == NUDGE
    # Message format: "9 weighted calls used. Checkpoint required at N."
    # Bash carries weight 1.0, so weighted matches raw and the helper omits
    # the "(raw N)" suffix.
    assert "9 weighted calls" in bv.reason


def test_block_at_checkpoint():
    for _ in range(13):
        bv = increment_and_check(SESSION, "Bash", False, _CFG)
    assert bv.action == BLOCK
    assert "Checkpoint" in bv.reason
    assert SESSION in bv.reason


def test_hard_block_clears_after_approve():
    for _ in range(21):
        bv = increment_and_check(SESSION, "Bash", False, _CFG)
    assert bv.action == BLOCK
    assert "Hard limit" in bv.reason
    assert SESSION in bv.reason

    approve(SESSION, add_tools=8)
    bv = increment_and_check(SESSION, "Bash", False, _CFG)
    assert bv.action != BLOCK


# ---------------------------------------------------------------------------
# Subagent gating
# ---------------------------------------------------------------------------

def test_subagent_blocked_by_default():
    bv = increment_and_check(SESSION, "Agent", False, _CFG)
    assert bv.action == BLOCK
    assert "Subagent blocked" in bv.reason
    assert SESSION in bv.reason


def test_subagent_allowed_after_approve():
    approve(SESSION, add_tools=8, approve_subagent=True)
    bv = increment_and_check(SESSION, "Agent", False, _CFG)
    assert bv.action != BLOCK or "Subagent" not in bv.reason


def test_subagent_allowed_when_max_subagents_nonzero():
    cfg = dict(_CFG, max_subagents=1)
    bv = increment_and_check(SESSION, "Agent", False, cfg)
    assert bv.action != BLOCK


def test_task_tool_also_counts_as_subagent():
    bv = increment_and_check(SESSION, "Task", False, _CFG)
    assert bv.action == BLOCK
    assert "Subagent" in bv.reason


# ---------------------------------------------------------------------------
# Large-read nudge
# ---------------------------------------------------------------------------

def test_large_read_nudge_after_quota():
    cfg = dict(_CFG, max_large_reads=2)
    # Two large reads: under quota
    for _ in range(2):
        bv = increment_and_check(SESSION, "Read", True, cfg)
    assert bv.action != NUDGE or "Large" not in bv.reason
    # Third: over quota
    bv = increment_and_check(SESSION, "Read", True, cfg)
    assert bv.action == NUDGE
    assert "Large" in bv.reason


def test_non_large_reads_dont_count():
    cfg = dict(_CFG, max_large_reads=1)
    for _ in range(3):
        bv = increment_and_check(SESSION, "Read", False, cfg)
    # should not trigger large-read nudge
    assert "Large" not in bv.reason


# ---------------------------------------------------------------------------
# approve()
# ---------------------------------------------------------------------------

def test_approve_extends_ceiling():
    # Get to checkpoint (call 13)
    for _ in range(13):
        increment_and_check(SESSION, "Bash", False, _CFG)

    state = approve(SESSION, add_tools=8)
    assert state["approved_tool_calls"] >= 13 + 8

    # Next call should now pass
    bv = increment_and_check(SESSION, "Bash", False, _CFG)
    assert bv.action != BLOCK or "Checkpoint" not in bv.reason


def test_approve_idempotent_ceiling():
    # Approve twice: ceiling should never decrease
    approve(SESSION, add_tools=20)
    state1 = read_state(SESSION)
    approve(SESSION, add_tools=5)
    state2 = read_state(SESSION)
    assert state2["approved_tool_calls"] >= state1["approved_tool_calls"]


def test_approve_subagent_flag():
    state = approve(SESSION, approve_subagent=True)
    assert state["subagent_approved"] is True

    bv = increment_and_check(SESSION, "Agent", False, _CFG)
    assert bv.action != BLOCK or "Subagent" not in bv.reason


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------

def test_reset_clears_state():
    for _ in range(15):
        increment_and_check(SESSION, "Bash", False, _CFG)

    deleted = reset(SESSION)
    assert deleted is True
    assert read_state(SESSION) == {}


def test_reset_nonexistent_returns_false():
    deleted = reset("no-such-session-xyz")
    assert deleted is False


def test_after_reset_counters_restart():
    # fill up past hard block
    approve(SESSION, add_tools=100)
    for _ in range(21):
        increment_and_check(SESSION, "Bash", False, _CFG)

    reset(SESSION)

    # should be back to normal after reset
    bv = increment_and_check(SESSION, "Bash", False, _CFG)
    assert bv.action == ALLOW


def test_reset_add_tools_pre_approves_ceiling():
    reset(SESSION, add_tools=20)
    state = read_state(SESSION)
    # approved_tool_calls should be checkpoint_at (12 in test cfg) + 20 = 32
    # but reset uses _DEFAULTS["checkpoint_at"] = 25, so ceiling = 25 + 20 = 45
    assert state["approved_tool_calls"] == 45
    assert state["tool_calls"] == 0


def test_reset_add_tools_zero_leaves_no_file():
    for _ in range(5):
        increment_and_check(SESSION, "Bash", False, _CFG)
    reset(SESSION, add_tools=0)
    assert read_state(SESSION) == {}


# ---------------------------------------------------------------------------
# read_state()
# ---------------------------------------------------------------------------

def test_read_state_returns_empty_when_missing():
    assert read_state("no-such-session-abc") == {}


def test_read_state_reflects_increments():
    increment_and_check(SESSION, "Bash", False, _CFG)
    increment_and_check(SESSION, "Bash", False, _CFG)
    state = read_state(SESSION)
    assert state["tool_calls"] == 2


# ---------------------------------------------------------------------------
# Fail-open paths
# ---------------------------------------------------------------------------

def test_corrupt_state_file_allows(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    path = _budget_path(SESSION)
    path.write_text("not json at all {{{{", encoding="utf-8")
    bv = increment_and_check(SESSION, "Bash", False, _CFG)
    # Should fail-open (ALLOW) or treat as fresh state (also ALLOW for call 1)
    assert bv.action in (ALLOW, NUDGE)


def test_bad_cfg_values_dont_raise():
    bad_cfg = {"nudge_at": "oops", "checkpoint_at": None, "hard_block_at": "bad"}
    bv = increment_and_check(SESSION, "Bash", False, bad_cfg)
    assert bv.action in (ALLOW, NUDGE, BLOCK)  # anything but an exception


def test_disabled_budget_not_checked():
    cfg = dict(_CFG, enabled=False)
    # We test disabled flag at the tripwire level; budget.py itself doesn't read it.
    # Confirm increment_and_check still works when called with it (caller filters).
    bv = increment_and_check(SESSION, "Bash", False, cfg)
    assert bv.action in (ALLOW, NUDGE, BLOCK)


def test_approve_returns_empty_on_bad_session(monkeypatch, tmp_path):
    # Point AGENT_RAILS_STATE_DIR at a regular file so _state_dir()'s mkdir fails.
    fake_file = tmp_path / "not-a-dir"
    fake_file.write_text("not a dir", encoding="utf-8")
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(fake_file))
    state = approve("bad-session", add_tools=8)
    assert state == {}


# ---------------------------------------------------------------------------
# self_approve()
# ---------------------------------------------------------------------------

_SA_CFG = {"enabled": True, "max_add": 3, "max_times_per_session": 2}


def test_self_approve_works_within_limits():
    # Hit checkpoint first
    for _ in range(13):
        increment_and_check(SESSION, "Bash", False, _CFG)
    result = self_approve(SESSION, add_tools=2, cfg=_SA_CFG)
    assert result["ok"] is True
    assert result["state"]["self_approve_times"] == 1
    assert result["state"]["approved_tool_calls"] >= 13 + 2


def test_self_approve_rejects_oversized_add():
    result = self_approve(SESSION, add_tools=5, cfg=_SA_CFG)
    assert result["ok"] is False
    assert "max_add" in result["reason"]


def test_self_approve_rejects_when_exhausted():
    for _ in range(13):
        increment_and_check(SESSION, "Bash", False, _CFG)
    self_approve(SESSION, add_tools=2, cfg=_SA_CFG)
    # extend ceiling so second call doesn't re-checkpoint
    approve(SESSION, add_tools=10)
    self_approve(SESSION, add_tools=2, cfg=_SA_CFG)
    # third self-approve should be rejected
    result = self_approve(SESSION, add_tools=2, cfg=_SA_CFG)
    assert result["ok"] is False
    assert "exhausted" in result["reason"]


def test_self_approve_disabled():
    cfg = dict(_SA_CFG, enabled=False)
    result = self_approve(SESSION, add_tools=2, cfg=cfg)
    assert result["ok"] is False
    assert "disabled" in result["reason"]


def test_self_approve_bad_cfg_fails_open():
    result = self_approve(SESSION, add_tools=2, cfg=None)  # type: ignore[arg-type]
    assert result["ok"] is False  # disabled (no enabled=True)


def test_self_approve_increments_counter():
    result1 = self_approve(SESSION, add_tools=1, cfg=_SA_CFG)
    assert result1["ok"] is True
    result2 = self_approve(SESSION, add_tools=1, cfg=_SA_CFG)
    assert result2["ok"] is True
    assert result2["state"]["self_approve_times"] == 2


# ---------------------------------------------------------------------------
# Checkpoint block message — self-approve option presence
# ---------------------------------------------------------------------------

_CFG_WITH_SA = dict(_CFG, self_approve={"enabled": True, "max_add": 3, "max_times_per_session": 2})
_CFG_SA_DISABLED = dict(_CFG, self_approve={"enabled": False, "max_add": 3, "max_times_per_session": 2})


def test_checkpoint_block_shows_self_approve_option():
    for _ in range(13):
        bv = increment_and_check(SESSION, "Bash", False, _CFG_WITH_SA)
    assert bv.action == BLOCK
    assert "Self-approve" in bv.reason
    assert "--self" in bv.reason
    assert "Human approval" in bv.reason


def test_checkpoint_block_no_self_approve_when_disabled():
    for _ in range(13):
        bv = increment_and_check(SESSION, "Bash", False, _CFG_SA_DISABLED)
    assert bv.action == BLOCK
    assert "Self-approve" not in bv.reason
    assert f"! agent-rails budget {SESSION} add N" in bv.reason


def test_checkpoint_block_no_self_approve_when_exhausted():
    cfg = dict(_CFG, self_approve={"enabled": True, "max_add": 3, "max_times_per_session": 1})
    # Seed the session so ceiling comes from cfg.checkpoint_at (12), not _DEFAULTS
    increment_and_check(SESSION, "Bash", False, cfg)  # tc=1, approved_tc=12
    # Use up the one self-approve slot; ceiling stays at max(12, 1+1)=12
    self_approve(SESSION, add_tools=1, cfg=cfg["self_approve"])
    # Drive past the checkpoint (tc=1 → 14; 14 > approved_tc=12 → BLOCK)
    for _ in range(12):
        increment_and_check(SESSION, "Bash", False, cfg)
    bv = increment_and_check(SESSION, "Bash", False, cfg)
    assert bv.action == BLOCK
    assert "Self-approve" not in bv.reason


# ---------------------------------------------------------------------------
# Skip-poll when self-approve is available (inline self-approve fast path)
# ---------------------------------------------------------------------------

def test_checkpoint_skips_poll_when_self_approve_available(monkeypatch):
    """Inline-self-approve flow: with sa enabled+remaining, no polling happens.

    Without this, the agent would wait poll_timeout_s seconds before being told
    to self-approve — exactly the friction we're removing.
    """
    cfg = dict(_CFG_WITH_SA, poll_timeout_s=30)  # non-zero so a skip is meaningful
    called: list = []

    def fake_poll(*args, **kwargs):
        called.append(args)
        return False

    monkeypatch.setattr("agent_rails.core.budget._poll_for_approval", fake_poll)
    for _ in range(13):
        bv = increment_and_check(SESSION, "Bash", False, cfg)
    assert bv.action == BLOCK
    assert called == []  # polling never invoked
    # Reframed message: instruction-style with --self leading
    assert "Self-approve" in bv.reason
    assert "--self" in bv.reason
    assert "then retry" in bv.reason.lower()


def test_checkpoint_polls_when_self_approve_unavailable(monkeypatch):
    """Human-approval path is preserved — polling still happens when sa is off."""
    cfg = dict(_CFG_SA_DISABLED, poll_timeout_s=30)
    called: list = []

    def fake_poll(*args, **kwargs):
        called.append(args)
        return False

    monkeypatch.setattr("agent_rails.core.budget._poll_for_approval", fake_poll)
    for _ in range(13):
        bv = increment_and_check(SESSION, "Bash", False, cfg)
    assert bv.action == BLOCK
    assert called  # poll was attempted


# ---------------------------------------------------------------------------
# Self-approve replenishment
# ---------------------------------------------------------------------------

_CFG_REPLENISH = dict(
    _CFG,
    self_approve={
        "enabled": True,
        "max_add": 3,
        "max_times_per_session": 2,
        "replenish_every": 5,
    },
)


def test_self_approve_replenishment_restores_slot():
    """After max_times is hit, replenish_every paced tool calls earn a slot back.

    Production callers (CLI) pass the wrapping budget cfg so checkpoint_at is
    in scope for replenishment math; this test mirrors that.
    """
    # Hit first checkpoint (tc=13)
    for _ in range(13):
        increment_and_check(SESSION, "Bash", False, _CFG_REPLENISH)
    # Burn both slots
    assert self_approve(SESSION, add_tools=1, cfg=_CFG_REPLENISH)["ok"] is True
    assert self_approve(SESSION, add_tools=1, cfg=_CFG_REPLENISH)["ok"] is True
    # Third attempt rejected at tc=13 — replenished=(13-12)//5=0
    rejected = self_approve(SESSION, add_tools=1, cfg=_CFG_REPLENISH)
    assert rejected["ok"] is False
    assert "exhausted" in rejected["reason"]
    # Extend ceiling so further tool calls don't re-checkpoint, then drive tc to 17
    approve(SESSION, add_tools=20)
    for _ in range(4):  # tc 13 -> 17, replenished=(17-12)//5=1
        increment_and_check(SESSION, "Bash", False, _CFG_REPLENISH)
    # Replenishment grants one slot back
    granted = self_approve(SESSION, add_tools=1, cfg=_CFG_REPLENISH)
    assert granted["ok"] is True
    assert granted["state"]["self_approve_times"] == 3


def test_replenish_every_zero_disables_replenishment():
    """replenish_every=0 means no replenishment ever — original strict cap."""
    cfg = dict(_CFG, self_approve={
        "enabled": True,
        "max_add": 3,
        "max_times_per_session": 1,
        "replenish_every": 0,
    })
    for _ in range(13):
        increment_and_check(SESSION, "Bash", False, cfg)
    self_approve(SESSION, add_tools=1, cfg=cfg)
    approve(SESSION, add_tools=20)
    for _ in range(5):  # plenty of paced calls
        increment_and_check(SESSION, "Bash", False, cfg)
    rejected = self_approve(SESSION, add_tools=1, cfg=cfg)
    assert rejected["ok"] is False
    assert "exhausted" in rejected["reason"]


# ---------------------------------------------------------------------------
# Quota-aware checkpoint relief (fail-open input from a harness quota probe)
# ---------------------------------------------------------------------------

class _Reading:
    """QuotaReading-like stand-in (the real one lives in adapters/codex/quota)."""

    def __init__(self, window_used_pct=None, weekly_used_pct=None):
        self.window_used_pct = window_used_pct
        self.weekly_used_pct = weekly_used_pct


def _drive_to_checkpoint(session, cfg, reading=None):
    """Run one call past checkpoint_at and return that call's verdict."""
    # checkpoint fires when weighted > approved (== checkpoint_at) -> the
    # (checkpoint_at + 1)th unit-weight call.
    verdict = None
    for _ in range(cfg["checkpoint_at"] + 1):
        verdict = increment_and_check(session, "Edit", False, cfg, quota_reading=reading)
    return verdict


def test_none_reading_preserves_checkpoint_block():
    # Regression guard: default (no reading) must behave exactly as before.
    v = _drive_to_checkpoint("q-none", _CFG, reading=None)
    assert v.action == BLOCK
    assert "Checkpoint" in v.reason


def test_low_quota_defers_checkpoint():
    v = _drive_to_checkpoint("q-low", _CFG, reading=_Reading(6.0, 30.0))
    assert v.action == NUDGE
    assert "deferred" in v.reason.lower()
    assert "window 6%" in v.reason and "weekly 30%" in v.reason


def test_high_quota_does_not_defer():
    # Either axis above the threshold -> no relief, checkpoint stands.
    v = _drive_to_checkpoint("q-high", _CFG, reading=_Reading(6.0, 90.0))
    assert v.action == BLOCK


def test_partial_reading_no_relief():
    # weekly unknown (null rate_limits path) -> conservative: no relief.
    v = _drive_to_checkpoint("q-partial", _CFG, reading=_Reading(6.0, None))
    assert v.action == BLOCK


def test_dict_reading_supported():
    v = _drive_to_checkpoint("q-dict", _CFG, reading={"window_used_pct": 5.0, "weekly_used_pct": 10.0})
    assert v.action == NUDGE
    assert "deferred" in v.reason.lower()


def test_relief_disabled_by_zero_threshold():
    cfg = dict(_CFG, quota_relief_below_pct=0)
    v = _drive_to_checkpoint("q-disabled", cfg, reading=_Reading(1.0, 1.0))
    assert v.action == BLOCK


def test_relief_never_bypasses_hard_limit():
    # Low quota must NOT relieve the hard limit — it is the ultimate backstop.
    v = None
    for _ in range(_CFG["hard_block_at"] + 1):
        v = increment_and_check("q-hard", "Edit", False, _CFG, quota_reading=_Reading(1.0, 1.0))
    assert v.action == BLOCK
    assert "Hard limit" in v.reason


def test_bool_pct_rejected_no_relief():
    # isinstance(True, int) is True; used_percent=True must not read as 1.0%.
    v = _drive_to_checkpoint("q-bool", _CFG, reading=_Reading(True, True))
    assert v.action == BLOCK


# ---------------------------------------------------------------------------
# Context-fill nudge (advisory; fed by either harness's quota reading)
# ---------------------------------------------------------------------------

class _CtxReading:
    def __init__(self, context_used_pct=None, window_used_pct=None, weekly_used_pct=None):
        self.context_used_pct = context_used_pct
        self.window_used_pct = window_used_pct
        self.weekly_used_pct = weekly_used_pct


def test_high_context_fill_nudges():
    v = increment_and_check("ctx-hi", "Read", False, _CFG, quota_reading=_CtxReading(85.0))
    assert v.action == NUDGE
    assert "Context ~85% full" in v.reason


def test_low_context_fill_no_nudge():
    v = increment_and_check("ctx-lo", "Read", False, _CFG, quota_reading=_CtxReading(20.0))
    assert v.action == ALLOW


def test_context_nudge_disabled_by_out_of_range():
    cfg = dict(_CFG, context_nudge_pct=0)
    v = increment_and_check("ctx-off", "Read", False, cfg, quota_reading=_CtxReading(99.0))
    assert v.action == ALLOW


def test_context_nudge_never_overrides_block():
    # A checkpoint block must take priority over a context nudge.
    r = _CtxReading(99.0)
    v = None
    for _ in range(_CFG["checkpoint_at"] + 1):
        v = increment_and_check("ctx-block", "Edit", False, _CFG, quota_reading=r)
    assert v.action == BLOCK


def test_none_reading_no_context_nudge():
    v = increment_and_check("ctx-none", "Read", False, _CFG, quota_reading=None)
    assert v.action == ALLOW
