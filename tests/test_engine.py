"""Engine tests — aggregation, observe/enforce/off modes, fail-open.

Uses a temp state dir (via AGENT_RAILS_STATE_DIR) so it never touches a real
session log.
"""
import os
import sys
import tempfile
from copy import deepcopy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# isolate state before importing anything that reads it
_TMP = tempfile.mkdtemp(prefix="agent-rails-test-")
os.environ["AGENT_RAILS_STATE_DIR"] = _TMP

from agent_rails.core.engine import evaluate  # noqa: E402
from agent_rails.core.events import ERROR, OK, PENDING, ToolEvent  # noqa: E402
from agent_rails.core.state import append_event  # noqa: E402
from agent_rails.detectors.base import ALLOW, BLOCK, NUDGE  # noqa: E402

BASE = {
    "window": 12,
    "detectors": {
        "repetition": {"enabled": True, "nudge_at": 3, "block_at": 4},
        "error_streak": {"enabled": True, "nudge_at": 3, "block_at": 6},
    },
}


def cfg(mode):
    c = deepcopy(BASE)
    c["mode"] = mode
    return c


def seed(session, n, tool="Bash", arg="a", status=OK, output_hash=""):
    for _ in range(n):
        append_event(ToolEvent(session, tool, arg, status, 0.0, output_hash=output_hash))


def cand(session, tool="Bash", arg="a"):
    return ToolEvent(session, tool, arg, PENDING, 0.0)


def test_enforce_mode_blocks_repetition():
    s = "enforce-rep"
    seed(s, 3, arg="a", status=ERROR)
    v = evaluate(s, cfg("enforce"), candidate=cand(s, arg="a"))
    assert v.action == BLOCK
    assert v.would_block is False
    assert v.response == "tripwire"


def test_observe_mode_downgrades_block_to_nudge():
    s = "observe-rep"
    seed(s, 3, arg="a", status=ERROR)
    v = evaluate(s, cfg("observe"), candidate=cand(s, arg="a"))
    assert v.action == NUDGE
    assert v.would_block is True  # carried as structured data, not prose
    assert v.response == "tripwire"


def test_detector_enforce_overrides_global_observe():
    s = "detector-enforce"
    seed(s, 3, arg="a", status=ERROR)
    c = cfg("observe")
    c["detectors"]["repetition"]["mode"] = "enforce"
    v = evaluate(s, c, candidate=cand(s, arg="a"))
    assert v.action == BLOCK
    assert v.would_block is False


def test_detector_observe_overrides_global_enforce():
    s = "detector-observe"
    seed(s, 3, arg="a", status=ERROR)
    c = cfg("enforce")
    c["detectors"]["repetition"]["mode"] = "observe"
    v = evaluate(s, c, candidate=cand(s, arg="a"))
    assert v.action == NUDGE
    assert v.would_block is True


def test_enforced_block_beats_observe_would_block():
    s = "mixed-mode-tie"
    seed(s, 3, arg="a", status=ERROR)  # repetition block + error_streak nudge/block context
    c = cfg("observe")
    c["detectors"]["repetition"]["mode"] = "observe"
    c["detectors"]["error_streak"]["mode"] = "enforce"
    c["detectors"]["error_streak"]["block_at"] = 3
    v = evaluate(s, c, candidate=cand(s, arg="a"))
    assert v.action == BLOCK
    assert v.detector == "error_streak"
    assert v.would_block is False


def test_detector_off_skips_even_when_global_enforce():
    s = "detector-off"
    seed(s, 3, arg="a", status=ERROR)
    c = cfg("enforce")
    c["detectors"] = {
        "repetition": {"enabled": True, "mode": "off", "nudge_at": 3, "block_at": 4},
        "error_streak": {"enabled": True, "nudge_at": 6, "block_at": 7},
    }
    v = evaluate(s, c, candidate=cand(s, arg="a"))
    assert v.action == ALLOW


def test_off_mode_always_allows():
    s = "off-rep"
    seed(s, 10, arg="a")
    v = evaluate(s, cfg("off"), candidate=cand(s, arg="a"))
    assert v.action == ALLOW


def test_highest_severity_wins():
    s = "agg"
    seed(s, 3, arg="a", status=ERROR)  # 3 identical AND 3 errors
    v = evaluate(s, cfg("enforce"), candidate=cand(s, arg="a"))
    assert v.action == BLOCK


def test_clean_session_allows():
    s = "clean"
    for i in range(5):
        append_event(ToolEvent(s, "Bash", f"arg{i}", OK, 0.0))
    v = evaluate(s, cfg("enforce"), candidate=cand(s, arg="fresh"))
    assert v.action == ALLOW


def test_workflow_wrapper_observe_mode_nudges_raw_gh_checks():
    s = "workflow-wrapper-observe"
    v = evaluate(
        s,
        cfg("observe"),
        candidate=ToolEvent(
            s, "Bash", "gh", PENDING, 0.0,
            arg_preview="gh pr checks 193 --json name,state,link",
        ),
    )
    assert v.action == NUDGE
    assert v.detector == "workflow_wrapper"
    assert v.would_block is True


def test_central_enable_gate_disables_detector():
    # repetition disabled in config -> engine skips it even though it would fire
    s = "gated"
    seed(s, 5, arg="a", status=OK, output_hash="same")
    c = cfg("enforce")
    c["detectors"] = {
        "repetition": {"enabled": False, "nudge_at": 3, "block_at": 4},
        "error_streak": {"enabled": True, "nudge_at": 3, "block_at": 6},
    }
    v = evaluate(s, c, candidate=cand(s, arg="a"))
    assert v.action == ALLOW


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
