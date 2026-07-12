"""Oscillation detector tests — short repeating cycles repetition misses."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hamingja.core.events import OK, PENDING, ToolEvent  # noqa: E402
from hamingja.detectors.base import BLOCK, NUDGE  # noqa: E402
from hamingja.detectors.oscillation import OscillationDetector  # noqa: E402

CFG = {
    "detectors": {
        "oscillation": {"enabled": True, "nudge_at": 4, "block_at": 6},
        "repetition": {"exempt_tools": ["Read", "Grep"]},
    }
}


def ev(tool="Bash", arg="x", status=OK):
    return ToolEvent("s", tool, arg, status, 0.0)


def _seq(args, tool="Bash"):
    return [ev(tool=tool, arg=a) for a in args]


def test_period2_block_at_three_cycles():
    # A B A B A + candidate B  -> 6 elements, period 2
    hist = _seq(["a", "b", "a", "b", "a"])
    cand = ev(arg="b", status=PENDING)
    v = OscillationDetector().evaluate(hist, cand, CFG)
    assert v is not None and v.action == BLOCK


def test_period2_nudge_at_two_cycles():
    # A B A + candidate B -> 4 elements
    hist = _seq(["a", "b", "a"])
    cand = ev(arg="b", status=PENDING)
    v = OscillationDetector().evaluate(hist, cand, CFG)
    assert v is not None and v.action == NUDGE


def test_period3_cycle_detected():
    # A B C A B + candidate C -> 6 elements, period 3
    hist = _seq(["a", "b", "c", "a", "b"])
    cand = ev(arg="c", status=PENDING)
    v = OscillationDetector().evaluate(hist, cand, CFG)
    assert v is not None and v.action == BLOCK


def test_pure_repetition_is_not_oscillation():
    # A A A A A -> single distinct key, left to the repetition detector
    hist = _seq(["a", "a", "a", "a", "a"])
    cand = ev(arg="a", status=PENDING)
    assert OscillationDetector().evaluate(hist, cand, CFG) is None


def test_progressing_sequence_is_quiet():
    hist = _seq(["a", "b", "c", "d", "e"])
    cand = ev(arg="f", status=PENDING)
    assert OscillationDetector().evaluate(hist, cand, CFG) is None


def test_exempt_only_loop_is_quiet():
    # alternately re-reading two files to compare them is not flailing
    hist = [ev(tool="Read", arg="f1"), ev(tool="Read", arg="f2"),
            ev(tool="Read", arg="f1"), ev(tool="Read", arg="f2"),
            ev(tool="Read", arg="f1")]
    cand = ev(tool="Read", arg="f2", status=PENDING)
    assert OscillationDetector().evaluate(hist, cand, CFG) is None


def test_disabled_is_quiet():
    cfg = {"detectors": {"oscillation": {"enabled": False}}}
    hist = _seq(["a", "b", "a", "b", "a"])
    cand = ev(arg="b", status=PENDING)
    assert OscillationDetector().evaluate(hist, cand, cfg) is None


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
