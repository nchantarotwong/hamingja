"""Progress assessment + budget credit.

The definition of "progress" is the security boundary of the progress-aware
budget: it must credit observed verification (a test/build going red->green, a
real error streak breaking) and must NOT credit tool success, novelty, or
self-assertion. These tests pin that boundary down — especially the negative
cases, which are the ones that would refuel a doom loop if they regressed.

Standalone-runnable (no pytest): `python tests/test_progress_credit.py`.
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager

from agent_rails.core import budget
from agent_rails.core.events import ToolEvent, OK, ERROR, BLOCKED
from agent_rails.core.progress import (
    admit_structured_progress,
    assess_progress,
    TEST_RECOVERY,
    STREAK_BROKEN,
    CLEAN_VALIDATION,
)


def ev(status: str, *, arg_kind: str = "", arg_hash: str = "h", tool: str = "Bash") -> ToolEvent:
    return ToolEvent(
        session_id="s", tool=tool, arg_hash=arg_hash, status=status, ts=0.0,
        arg_kind=arg_kind,
    )


@contextmanager
def temp_state():
    """Isolate budget/state files in a throwaway dir for one test."""
    prev = os.environ.get("AGENT_RAILS_STATE_DIR")
    with tempfile.TemporaryDirectory() as d:
        os.environ["AGENT_RAILS_STATE_DIR"] = d
        try:
            yield d
        finally:
            if prev is None:
                os.environ.pop("AGENT_RAILS_STATE_DIR", None)
            else:
                os.environ["AGENT_RAILS_STATE_DIR"] = prev


def _budget_cfg() -> dict:
    # poll_timeout_s=0 makes a blocked checkpoint return immediately instead of
    # waiting 60s for an approver that no test provides.
    return {"checkpoint_at": 12, "hard_block_at": 20, "nudge_at": 8, "poll_timeout_s": 0}


# --- positive signals -------------------------------------------------------

def test_red_to_green_test_is_strongest_credit():
    events = [ev(ERROR, arg_kind="shell:test"), ev(OK, arg_kind="shell:test")]
    sig = assess_progress(events, {})
    assert sig is not None
    assert sig.kind == TEST_RECOVERY
    assert sig.credit == 12.0


def test_red_to_green_build_recovers():
    events = [ev(ERROR, arg_kind="shell:build"), ev(OK, arg_kind="shell:build")]
    sig = assess_progress(events, {})
    assert sig is not None and sig.kind == TEST_RECOVERY


def test_unrelated_green_validation_does_not_clear_failed_signature():
    events = [
        ev(ERROR, arg_kind="shell:test", arg_hash="target-a"),
        ev(OK, arg_kind="shell:test", arg_hash="target-b"),
    ]
    assert assess_progress(events, {}) is None


def test_clean_validation_when_nothing_was_failing():
    events = [ev(OK, tool="Read", arg_kind="read"), ev(OK, arg_kind="shell:test", arg_hash="t1")]
    sig = assess_progress(events, {})
    assert sig is not None
    assert sig.kind == CLEAN_VALIDATION
    assert sig.credit == 3.0


def test_breaking_a_real_error_streak_credits():
    events = [ev(ERROR, arg_hash="a"), ev(ERROR, arg_hash="b"), ev(OK, arg_hash="c")]
    sig = assess_progress(events, {})
    assert sig is not None
    assert sig.kind == STREAK_BROKEN
    assert sig.credit == 6.0


# --- the negative cases that protect against refueling a loop ---------------

def test_successful_edit_is_not_progress():
    # tool exit 0 != task advanced. A successful mutation after a successful
    # one is steady-state and must earn nothing.
    events = [ev(OK, tool="Edit", arg_kind="edit"), ev(OK, tool="Edit", arg_kind="edit", arg_hash="h2")]
    assert assess_progress(events, {}) is None


def test_single_error_then_unrelated_success_is_not_a_streak_break():
    events = [ev(ERROR, arg_hash="a"), ev(OK, arg_hash="b")]
    assert assess_progress(events, {}) is None  # streak of 1 < streak_broken_min


def test_rerunning_a_green_test_does_not_pay_twice():
    events = [ev(OK, arg_kind="shell:test", arg_hash="t1"), ev(OK, arg_kind="shell:test", arg_hash="t1")]
    assert assess_progress(events, {}) is None  # dedupe: same check already passed


def test_a_failure_never_credits():
    events = [ev(OK, arg_kind="shell:test"), ev(ERROR, arg_kind="shell:test")]
    assert assess_progress(events, {}) is None


def test_blocked_marker_is_not_an_error_streak():
    # A BLOCKED denial is not an ERROR, so a success after it is not a recovery.
    events = [ev(BLOCKED, arg_hash="a"), ev(BLOCKED, arg_hash="b"), ev(OK, arg_hash="c")]
    assert assess_progress(events, {}) is None


def test_disabled_progress_returns_none():
    events = [ev(ERROR, arg_kind="shell:test"), ev(OK, arg_kind="shell:test")]
    assert assess_progress(events, {"progress": {"enabled": False}}) is None


def test_empty_window_returns_none():
    assert assess_progress([], {}) is None


def test_credit_magnitudes_are_configurable():
    events = [ev(ERROR, arg_kind="shell:test"), ev(OK, arg_kind="shell:test")]
    sig = assess_progress(events, {"progress": {"test_recovery_credit": 99}})
    assert sig is not None and sig.credit == 99.0


def test_structured_failure_shrink_requires_observed_anchor_and_counts():
    events = [ev(ERROR, arg_hash="validation-anchor")]
    evidence = {
        "kind": "failure_set_shrank",
        "anchor": "validation-anchor",
        "validation_id": "pytest:targeted",
        "failure_count_before": 12,
        "failure_count_after": 3,
    }
    sig = admit_structured_progress(evidence, events, {})
    assert sig is not None and sig.credit == 12.0
    assert sig.evidence["validation_id"] == "pytest:targeted"


def test_structured_progress_rejects_missing_anchor_and_non_shrink():
    events = [ev(ERROR, arg_hash="real-anchor")]
    base = {
        "kind": "failure_set_shrank",
        "anchor": "invented-anchor",
        "validation_id": "pytest:targeted",
        "failure_count_before": 3,
        "failure_count_after": 3,
    }
    assert admit_structured_progress(base, events, {}) is None
    base["anchor"] = "real-anchor"
    assert admit_structured_progress(base, events, {}) is None


# --- credit_progress against real budget state ------------------------------

def test_credit_decrements_weighted_calls():
    with temp_state():
        sid = "sess-credit"
        cfg = _budget_cfg()
        for _ in range(6):
            budget.increment_and_check(sid, "Edit", False, cfg)
        before = budget.read_state(sid)["weighted_calls"]
        budget.credit_progress(sid, 4.0)
        after = budget.read_state(sid)["weighted_calls"]
        assert after == before - 4.0


def test_credit_clamps_at_zero():
    with temp_state():
        sid = "sess-clamp"
        budget.increment_and_check(sid, "Edit", False, _budget_cfg())
        budget.credit_progress(sid, 9999.0)
        assert budget.read_state(sid)["weighted_calls"] == 0.0


def test_credit_on_missing_state_is_noop():
    with temp_state():
        assert budget.credit_progress("never-spent", 5.0) == {}


def test_nonpositive_credit_is_ignored():
    with temp_state():
        sid = "sess-zero"
        budget.increment_and_check(sid, "Edit", False, _budget_cfg())
        assert budget.credit_progress(sid, 0.0) == {}
        assert budget.credit_progress(sid, -3.0) == {}


def test_does_not_touch_raw_tool_calls():
    # tool_calls anchors the approval ceiling + detector history; credit must
    # only move the live weighted counter, never the raw count.
    with temp_state():
        sid = "sess-raw"
        cfg = _budget_cfg()
        for _ in range(5):
            budget.increment_and_check(sid, "Edit", False, cfg)
        raw_before = budget.read_state(sid)["tool_calls"]
        budget.credit_progress(sid, 3.0)
        assert budget.read_state(sid)["tool_calls"] == raw_before


def test_progress_buys_headroom_past_a_checkpoint():
    # End-to-end: spend up to the checkpoint, then a red->green credit should
    # drop the live counter back under the ceiling so work continues.
    with temp_state():
        sid = "sess-e2e"
        cfg = _budget_cfg()  # checkpoint_at 12
        verdict = None
        for _ in range(13):
            verdict = budget.increment_and_check(sid, "Edit", False, cfg)
        assert verdict.action == budget.BLOCK  # tripped the checkpoint
        budget.credit_progress(sid, 12.0)  # a test recovered
        assert budget.read_state(sid)["weighted_calls"] <= cfg["checkpoint_at"]


def test_cap_limits_total_credit_within_a_window():
    with temp_state():
        sid = "sess-cap"
        cfg = _budget_cfg()
        for _ in range(30):  # tool_calls=30, weighted_calls=30
            budget.increment_and_check(sid, "Edit", False, cfg)
        budget.credit_progress(sid, 12, max_per_window=12, window=12)  # 30 -> 18
        budget.credit_progress(sid, 12, max_per_window=12, window=12)  # capped: no relief
        assert budget.read_state(sid)["weighted_calls"] == 18.0


def test_cap_resets_once_the_window_advances():
    with temp_state():
        sid = "sess-cap2"
        cfg = _budget_cfg()
        for _ in range(30):
            budget.increment_and_check(sid, "Edit", False, cfg)
        budget.credit_progress(sid, 12, max_per_window=12, window=12)  # 30 -> 18
        budget.credit_progress(sid, 12, max_per_window=12, window=12)  # capped
        assert budget.read_state(sid)["weighted_calls"] == 18.0
        for _ in range(13):  # advance past the window so the old credit ages out
            budget.increment_and_check(sid, "Edit", False, cfg)  # wc 18 -> 31, tc 30 -> 43
        budget.credit_progress(sid, 12, max_per_window=12, window=12)  # 31 -> 19
        assert budget.read_state(sid)["weighted_calls"] == 19.0


def test_cap_applies_partial_remaining_allowance():
    # used=6 of cap=12 leaves 6; a 12-credit must apply only the 6 remaining.
    with temp_state():
        sid = "sess-partial"
        cfg = _budget_cfg()
        for _ in range(30):
            budget.increment_and_check(sid, "Edit", False, cfg)
        budget.credit_progress(sid, 6, max_per_window=12, window=12)   # 30 -> 24, used=6
        budget.credit_progress(sid, 12, max_per_window=12, window=12)  # only 6 left -> 18
        assert budget.read_state(sid)["weighted_calls"] == 18.0


def test_cap_zero_means_unlimited():
    with temp_state():
        sid = "sess-nocap"
        cfg = _budget_cfg()
        for _ in range(30):
            budget.increment_and_check(sid, "Edit", False, cfg)
        budget.credit_progress(sid, 12, max_per_window=0, window=12)  # 30 -> 18
        budget.credit_progress(sid, 12, max_per_window=0, window=12)  # 18 -> 6
        assert budget.read_state(sid)["weighted_calls"] == 6.0


def test_shipped_defaults_have_allowance_and_cap():
    # Pin the shipped contract: subagents get a small allowance (cure for
    # context poisoning is not taxed per spawn), and the credit cap is on.
    from agent_rails.config import load_config
    with temp_state() as d:
        cfg = load_config(d)  # no project config in a fresh temp dir -> baseline
        assert cfg["budget"]["max_subagents"] == 1
        assert cfg["budget"]["progress"]["max_credit_per_window"] == 12


def test_record_wires_credit_through_real_config():
    # Integration: drive the public record() path with a red->green pytest
    # sequence and confirm the live counter actually drops. Exercises real
    # config load + arg-kind classification + append + assess + credit, which
    # the isolated unit tests above do not.
    from agent_rails.core.api import record

    with temp_state() as d:
        sid = "sess-record"
        # Seed spend up to 15 (poll_timeout_s=0 so a past-checkpoint call
        # returns immediately instead of waiting for an approver).
        for _ in range(15):
            budget.increment_and_check(sid, "Bash", False, _budget_cfg())
        before = budget.read_state(sid)["weighted_calls"]
        assert before == 15.0

        # A failing test run, then the same test green. project_dir=d isolates
        # config to the packaged baseline (no stray project overlay).
        record(sid, "Bash", {"command": "pytest"}, ok=False, project_dir=d)
        record(sid, "Bash", {"command": "pytest"}, ok=True, project_dir=d)

        after = budget.read_state(sid)["weighted_calls"]
        # red->green credited 12 (the default test_recovery_credit).
        assert after == before - 12.0


def test_record_progress_persists_scoped_evidence():
    from agent_rails.core.api import record, record_progress

    with temp_state() as d:
        sid = "sess-structured-progress"
        for _ in range(15):
            budget.increment_and_check(sid, "Bash", False, _budget_cfg())
        args = {"command": "pytest tests/test_target.py"}
        record(sid, "Bash", args, ok=False, project_dir=d, output="12 failed")
        anchor = ToolEvent.record(sid, "Bash", args, False, output="12 failed").arg_hash
        accepted = record_progress(sid, {
            "kind": "failure_set_shrank",
            "anchor": anchor,
            "validation_id": "pytest:tests/test_target.py",
            "failure_count_before": 12,
            "failure_count_after": 3,
        }, project_dir=d)
        state = budget.read_state(sid)
        assert accepted is True
        assert state["last_progress"]["failure_count_after"] == 3
        assert state["weighted_calls"] == 3.0


if __name__ == "__main__":
    import sys
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
