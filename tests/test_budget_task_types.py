"""Tests for per-task-type budgets and per-tool weight discounts."""
from __future__ import annotations

import json

import pytest

from agent_rails.core.budget import (
    ALLOW,
    BLOCK,
    NUDGE,
    _budget_path,
    _DEFAULT_TASK_TYPES,
    _DEFAULT_WEIGHTS,
    _effective_thresholds,
    _tool_weight,
    approve,
    get_task_type,
    increment_and_check,
    known_task_types,
    read_state,
    set_task_type,
)

SESSION = "test-budget-extensions"

_CFG = {
    "enabled": True,
    "nudge_at": 8,
    "checkpoint_at": 12,
    "hard_block_at": 20,
    "max_large_reads": 2,
    "max_subagents": 0,
    "poll_timeout_s": 0,
}


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    yield


# ---------------------------------------------------------------------------
# Weight resolution
# ---------------------------------------------------------------------------

def test_builtin_weights_cover_read_class_tools():
    # All the documented read-class tools should be discounted to 0.5
    for tool in ("Read", "Glob", "Grep", "LS", "NotebookRead", "TodoRead", "TaskList", "TaskGet"):
        assert _DEFAULT_WEIGHTS[tool] == 0.5, tool


def test_tool_weight_default_uses_builtin_discount():
    assert _tool_weight("Read", {}) == 0.5


def test_tool_weight_can_disable_builtin_discount():
    assert _tool_weight("Read", {"disable_default_weights": True}) == 1.0


def test_tool_weight_explicit_override_wins_when_builtin_discount_disabled():
    cfg = {"disable_default_weights": True, "weights": {"Read": 0.2}}
    assert _tool_weight("Read", cfg) == pytest.approx(0.2)


def test_tool_weight_default_key_applies_when_builtin_discount_disabled():
    cfg = {"disable_default_weights": True, "weights": {"_default": 0.25}}
    assert _tool_weight("SomeNewTool", cfg) == pytest.approx(0.25)


def test_tool_weight_unknown_tool_defaults_to_one():
    assert _tool_weight("Bash", {}) == 1.0
    assert _tool_weight("Edit", {}) == 1.0


def test_builtin_weights_cover_ledger_command_families():
    assert _tool_weight("Bash:agent-rails ledger check", {}) == 0.0
    assert _tool_weight("Bash:agent-rails ledger relevant", {}) == 0.0
    assert _tool_weight("Bash:agent-rails ledger add", {}) == pytest.approx(0.2)
    assert _tool_weight("Bash:agent-rails ledger retire", {}) == pytest.approx(0.2)
    assert _tool_weight("Bash:agent-rails ledger reverify", {}) == 1.0


def test_ledger_command_weight_user_override_wins():
    cfg = {"weights": {"Bash:agent-rails ledger add": 0.05}}
    assert _tool_weight("Bash:agent-rails ledger add", cfg) == pytest.approx(0.05)


def test_tool_weight_user_override_beats_builtin():
    cfg = {"weights": {"Read": 0.1}}
    assert _tool_weight("Read", cfg) == pytest.approx(0.1)


def test_tool_weight_default_key_applies_to_unknown_tools_only():
    # _default should NOT override an explicit built-in for a known read-class tool
    cfg = {"weights": {"_default": 0.25}}
    assert _tool_weight("Read", cfg) == 0.5, "built-in for Read should win over _default"
    assert _tool_weight("SomeNewTool", cfg) == pytest.approx(0.25)


def test_tool_weight_cfg_default_weight_used_when_no_weights_dict():
    cfg = {"default_weight": 0.7}
    assert _tool_weight("SomeNewTool", cfg) == pytest.approx(0.7)


def test_tool_weight_clamps_malformed_values():
    # Negative and absurdly large weights should clamp to [0.0, 10.0]
    assert _tool_weight("Bash", {"weights": {"Bash": -5}}) == 0.0
    assert _tool_weight("Bash", {"weights": {"Bash": 9999}}) == 10.0
    # Non-numeric falls back to 1.0
    assert _tool_weight("Bash", {"weights": {"Bash": "lots"}}) == 1.0


# ---------------------------------------------------------------------------
# Weighted counter math
# ---------------------------------------------------------------------------

def test_weighted_calls_increment_by_tool_weight():
    # 6 reads at 0.5 each = 3.0 weighted; raw stays at 6.
    for _ in range(6):
        increment_and_check(SESSION, "Read", False, _CFG)
    state = read_state(SESSION)
    assert state["tool_calls"] == 6
    assert state["weighted_calls"] == pytest.approx(3.0)


def test_ledger_read_commands_do_not_spend_weighted_budget():
    for _ in range(10):
        increment_and_check(SESSION, "Bash:agent-rails ledger relevant", False, _CFG)
    state = read_state(SESSION)
    assert state["tool_calls"] == 10
    assert state["weighted_calls"] == 0.0


def test_ledger_add_spends_low_nonzero_weighted_budget():
    for _ in range(5):
        increment_and_check(SESSION, "Bash:agent-rails ledger add", False, _CFG)
    state = read_state(SESSION)
    assert state["tool_calls"] == 5
    assert state["weighted_calls"] == pytest.approx(1.0)


def test_reads_dont_trip_checkpoint_when_writes_would_have():
    # 20 reads at 0.5 = 10.0 weighted, under checkpoint_at=12. No block.
    bv = None
    for _ in range(20):
        bv = increment_and_check(SESSION, "Read", False, _CFG)
    state = read_state(SESSION)
    assert state["weighted_calls"] == pytest.approx(10.0)
    assert state["tool_calls"] == 20
    assert bv.action != BLOCK


def test_writes_still_trip_checkpoint():
    # 13 Bash (weight 1.0) goes over checkpoint_at=12.
    for _ in range(13):
        bv = increment_and_check(SESSION, "Bash", False, _CFG)
    assert bv.action == BLOCK
    assert "Checkpoint" in bv.reason
    assert "weighted" in bv.reason


# ---------------------------------------------------------------------------
# Task type API
# ---------------------------------------------------------------------------

def test_known_task_types_includes_all_builtins():
    types = known_task_types({})
    for name in ("trivial", "standard", "debug", "audit", "explore"):
        assert name in types


def test_known_task_types_picks_up_user_defined():
    cfg = {"task_types": {"migration": {"checkpoint_at": 40, "hard_block_at": 100}}}
    types = known_task_types(cfg)
    assert "migration" in types
    assert "standard" in types  # built-ins still present


def test_set_task_type_rejects_unknown_name():
    result = set_task_type(SESSION, "marketing", _CFG)
    assert not result["ok"]
    assert "unknown task type" in result["reason"]


def test_set_task_type_rejects_empty():
    result = set_task_type(SESSION, "", _CFG)
    assert not result["ok"]


def test_set_task_type_persists_and_get_reads_back():
    result = set_task_type(SESSION, "debug", _CFG)
    assert result["ok"]
    assert get_task_type(SESSION) == "debug"


def test_set_task_type_raises_ceiling_to_bucket_checkpoint():
    # debug bucket's checkpoint is 35 from the built-in defaults
    result = set_task_type(SESSION, "debug", _CFG)
    assert result["ok"]
    debug_checkpoint = _DEFAULT_TASK_TYPES["debug"]["checkpoint_at"]
    assert result["state"]["approved_tool_calls"] >= debug_checkpoint


def test_set_task_type_never_lowers_existing_ceiling():
    # Approve to a high ceiling, then set to "trivial" (built-in cp=10).
    # The ceiling should NOT drop.
    approve(SESSION, add_tools=80)
    state_before = read_state(SESSION)
    ceiling_before = state_before["approved_tool_calls"]
    set_task_type(SESSION, "trivial", _CFG)
    state_after = read_state(SESSION)
    assert state_after["approved_tool_calls"] == ceiling_before


# ---------------------------------------------------------------------------
# Per-type thresholds
# ---------------------------------------------------------------------------

def test_effective_thresholds_use_task_type_when_set():
    cp, hb = _effective_thresholds(_CFG, "debug")
    assert cp == _DEFAULT_TASK_TYPES["debug"]["checkpoint_at"]
    assert hb == _DEFAULT_TASK_TYPES["debug"]["hard_block_at"]


def test_effective_thresholds_fall_back_to_global_when_no_type():
    cp, hb = _effective_thresholds(_CFG, None)
    assert cp == _CFG["checkpoint_at"]
    assert hb == _CFG["hard_block_at"]


def test_effective_thresholds_user_type_overrides_builtin():
    cfg = dict(_CFG, task_types={"debug": {"checkpoint_at": 99, "hard_block_at": 200}})
    cp, hb = _effective_thresholds(cfg, "debug")
    assert cp == 99
    assert hb == 200


def test_effective_thresholds_raises_hb_to_cp_if_misconfigured():
    # An hb below cp would silently fail-open. The helper should clamp hb up.
    cfg = dict(_CFG, task_types={"backwards": {"checkpoint_at": 50, "hard_block_at": 5}})
    cp, hb = _effective_thresholds(cfg, "backwards")
    assert cp == 50
    assert hb == 50  # raised from 5


def test_debug_type_grants_more_runway_than_standard():
    # Same number of Bash calls under "debug" should not block where they would
    # block under defaults.
    set_task_type(SESSION, "debug", _CFG)
    bv = None
    for _ in range(13):  # would block under cp=12
        bv = increment_and_check(SESSION, "Bash", False, _CFG)
    assert bv.action != BLOCK


# ---------------------------------------------------------------------------
# Back-compat: old state files
# ---------------------------------------------------------------------------

def test_old_state_seeds_weighted_from_tool_calls():
    # Pre-write a state file in the OLD shape (no weighted_calls, no task_type).
    path = _budget_path(SESSION)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "tool_calls": 7,
        "subagents": 0,
        "large_reads": 0,
        "approved_tool_calls": 25,
        "subagent_approved": False,
        "self_approve_times": 0,
    }))
    # One new call. weighted_calls should pick up at 7 (seed) + 1.0 (Bash) = 8.0.
    increment_and_check(SESSION, "Bash", False, _CFG)
    state = read_state(SESSION)
    assert state["weighted_calls"] == pytest.approx(8.0)
    assert state["tool_calls"] == 8
    assert state["task_type"] is None


def test_old_state_with_invalid_task_type_field_is_ignored():
    # Garbage in task_type should be silently dropped, not crash.
    path = _budget_path(SESSION)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "tool_calls": 0,
        "approved_tool_calls": 25,
        "task_type": 12345,  # wrong type
    }))
    state = read_state(SESSION)
    # read_state returns raw JSON; check that loading via increment normalizes.
    increment_and_check(SESSION, "Bash", False, _CFG)
    state = read_state(SESSION)
    assert state["task_type"] is None


# ---------------------------------------------------------------------------
# Tripwire independence: weights don't affect repetition / error / oscillation
# detector state. We verify the raw tool_calls counter still ticks at 1 per
# call so detector history stays consistent.
# ---------------------------------------------------------------------------

def test_raw_tool_calls_unaffected_by_weights():
    for _ in range(10):
        increment_and_check(SESSION, "Read", False, _CFG)  # weight 0.5
    state = read_state(SESSION)
    assert state["tool_calls"] == 10
    assert state["weighted_calls"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Self-approve replenishment math under a weighted (float) counter
# ---------------------------------------------------------------------------

def test_sa_remaining_returns_int_when_weighted_counter_is_float():
    """`_sa_remaining(weighted_calls=..., ...)` is now called with a float.
    The returned slot count must still be an int so message templates
    render "1/2 uses remaining" rather than "1.0/2 uses remaining"."""
    from agent_rails.core.budget import _sa_remaining

    # weighted_calls 30.5, checkpoint_at 20, replenish_every 10 →
    # (30.5 - 20) // 10 = 1.0 (float); should cast to int.
    remaining = _sa_remaining(
        tool_calls=30.5,
        self_approve_times=1,
        checkpoint_at=20,
        max_times=2,
        replenish_every=10,
    )
    assert isinstance(remaining, int)
    assert remaining == 2  # one slot replenished + one unused
