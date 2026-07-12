"""Audit-log tests — observability behind observe mode.

Verifies that non-ALLOW verdicts are logged, ALLOW is not, the log survives a
corrupt line, summarize() separates would_block from nudge/block, and the whole
layer stays fail-open.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="agent-rails-audit-")
os.environ["AGENT_RAILS_STATE_DIR"] = _TMP

from agent_rails.core.audit import (  # noqa: E402
    clear_audit,
    log_verdict,
    read_audit,
    summarize,
)
from agent_rails.detectors.base import ALLOW, BLOCK, NUDGE, Verdict  # noqa: E402


def setup_function(_):
    clear_audit()


def test_allow_is_not_logged():
    log_verdict("s", "Bash", Verdict(ALLOW, "engine", ""))
    assert read_audit() == []


def test_nudge_and_block_are_logged():
    log_verdict("s", "Bash", Verdict(NUDGE, "repetition", "r"))
    log_verdict("s", "Bash", Verdict(BLOCK, "error_streak", "b"))
    entries = read_audit()
    assert [e["action"] for e in entries] == ["nudge", "block"]
    assert entries[0]["detector"] == "repetition"


def test_summarize_separates_would_block():
    log_verdict("s1", "Bash", Verdict(NUDGE, "repetition", "r"))  # plain nudge
    log_verdict("s1", "Bash", Verdict(NUDGE, "repetition", "r", would_block=True))
    log_verdict("s2", "Edit", Verdict(BLOCK, "error_streak", "b"))
    s = summarize(read_audit())
    assert s["total"] == 3
    assert s["sessions"] == 2
    assert s["nudges"] == 1
    assert s["would_blocks"] == 1
    assert s["blocks"] == 1
    assert s["by_detector"]["repetition"]["would_block"] == 1
    assert s["by_detector"]["error_streak"]["block"] == 1
    assert s["by_response"] == {"observe": 3}
    assert isinstance(s["first_ts"], float)
    assert s["last_ts"] >= s["first_ts"]


def test_summarize_malformed_timestamp_does_not_break_aggregates():
    result = summarize([{
        "session_id": "s", "detector": "repetition", "action": "nudge",
        "response": "advise", "ts": "bad",
    }])
    assert result["total"] == 1
    assert result["by_response"] == {"advise": 1}
    assert result["first_ts"] is None


def test_summarize_bounds_corrupt_labels_and_nonfinite_time():
    result = summarize([{
        "session_id": ["unhashable"], "detector": "x" * 1000,
        "action": "nudge", "response": "y" * 1000, "ts": float("inf"),
    }])
    assert result["total"] == 1
    assert len(next(iter(result["by_detector"]))) == 128
    assert len(next(iter(result["by_response"]))) == 128
    assert result["first_ts"] is None


def test_corrupt_line_is_skipped():
    log_verdict("s", "Bash", Verdict(NUDGE, "repetition", "r"))
    with open(os.path.join(_TMP, "_audit.jsonl"), "a", encoding="utf-8") as f:
        f.write("{not json\n")
    log_verdict("s", "Bash", Verdict(BLOCK, "error_streak", "b"))
    assert len(read_audit()) == 2  # the garbage line is silently dropped


def test_log_verdict_never_raises_on_bad_input():
    log_verdict("s", "Bash", None)  # must not raise
    assert read_audit() == []


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
