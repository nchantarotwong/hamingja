"""Detector unit tests — synthetic event sequences only.

NEVER replace these with captured real sessions: a real transcript can drag
private repo internals into git history. Detectors are pure functions over
ToolEvents, so synthetic sequences exercise them fully.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_rails.core.events import ERROR, OK, PENDING, ToolEvent  # noqa: E402
from agent_rails.detectors.base import ALLOW, BLOCK, NUDGE  # noqa: E402
from agent_rails.detectors.error_streak import ErrorStreakDetector  # noqa: E402
from agent_rails.detectors.repetition import RepetitionDetector  # noqa: E402

CFG = {
    "detectors": {
        "repetition": {"enabled": True, "nudge_at": 3, "block_at": 4},
        "error_streak": {"enabled": True, "nudge_at": 3, "block_at": 6},
    }
}


def ev(tool="Bash", arg="x", status=OK, sid="s"):
    return ToolEvent(sid, tool, arg, status, 0.0)


# --- repetition ---------------------------------------------------------

def test_repetition_blocks_the_fourth_identical_call():
    hist = [ev(arg="a") for _ in range(3)]
    cand = ev(arg="a", status=PENDING)
    v = RepetitionDetector().evaluate(hist, cand, CFG)
    assert v is not None and v.action == BLOCK


def test_repetition_nudges_on_the_third():
    hist = [ev(arg="a") for _ in range(2)]
    cand = ev(arg="a", status=PENDING)
    v = RepetitionDetector().evaluate(hist, cand, CFG)
    assert v is not None and v.action == NUDGE


def test_repetition_ignores_varied_calls():
    hist = [ev(arg=str(i)) for i in range(6)]
    cand = ev(arg="brand-new", status=PENDING)
    assert RepetitionDetector().evaluate(hist, cand, CFG) is None


def test_repetition_distinguishes_by_tool():
    hist = [ev(tool="Bash", arg="a") for _ in range(3)]
    cand = ev(tool="Read", arg="a", status=PENDING)
    assert RepetitionDetector().evaluate(hist, cand, CFG) is None


def test_repetition_exempts_read_only_tools():
    # repeating a read-only tool with identical args is normal, not flailing
    cfg = {"detectors": {"repetition": {
        "enabled": True, "nudge_at": 3, "block_at": 4, "exempt_tools": ["Read"]}}}
    hist = [ev(tool="Read", arg="a") for _ in range(5)]
    cand = ev(tool="Read", arg="a", status=PENDING)
    assert RepetitionDetector().evaluate(hist, cand, cfg) is None
    # a non-exempt tool with the same pattern still trips
    hist2 = [ev(tool="Bash", arg="a") for _ in range(5)]
    cand2 = ev(tool="Bash", arg="a", status=PENDING)
    assert RepetitionDetector().evaluate(hist2, cand2, cfg).action == BLOCK


def test_repetition_respects_disabled():
    cfg = {"detectors": {"repetition": {"enabled": False}}}
    hist = [ev(arg="a") for _ in range(5)]
    cand = ev(arg="a", status=PENDING)
    assert RepetitionDetector().evaluate(hist, cand, cfg) is None


# --- error streak -------------------------------------------------------

def test_error_streak_resets_on_success():
    hist = [ev(status=ERROR) for _ in range(5)] + [ev(status=OK), ev(status=ERROR)]
    assert ErrorStreakDetector().evaluate(hist, None, CFG) is None


def test_error_streak_nudges_at_three():
    hist = [ev(status=ERROR) for _ in range(3)]
    v = ErrorStreakDetector().evaluate(hist, None, CFG)
    assert v is not None and v.action == NUDGE


def test_error_streak_blocks_at_six():
    hist = [ev(status=ERROR) for _ in range(6)]
    v = ErrorStreakDetector().evaluate(hist, None, CFG)
    assert v is not None and v.action == BLOCK


def test_error_streak_clean_history_is_quiet():
    hist = [ev(status=OK) for _ in range(8)]
    assert ErrorStreakDetector().evaluate(hist, None, CFG) is None


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
