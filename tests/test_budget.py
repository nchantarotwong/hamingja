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
)

SESSION = "test-budget-session"

_CFG = {
    "enabled": True,
    "nudge_at": 8,
    "checkpoint_at": 12,
    "hard_block_at": 20,
    "max_large_reads": 2,
    "max_subagents": 0,
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
    assert "9 tool calls" in bv.reason


def test_block_at_checkpoint():
    for _ in range(13):
        bv = increment_and_check(SESSION, "Bash", False, _CFG)
    assert bv.action == BLOCK
    assert "Checkpoint" in bv.reason
    assert SESSION in bv.reason


def test_hard_block_unconditional():
    # Approve past checkpoint so normal blocks don't fire
    approve(SESSION, add_tools=100)
    for _ in range(21):
        bv = increment_and_check(SESSION, "Bash", False, _CFG)
    assert bv.action == BLOCK
    assert "Hard limit" in bv.reason
    assert SESSION in bv.reason


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
