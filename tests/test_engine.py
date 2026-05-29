"""Engine tests — aggregation, observe/enforce/off modes, fail-open.

Uses a temp state dir (via AGENT_RAILS_STATE_DIR) so it never touches a real
session log.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# isolate state before importing anything that reads it
_TMP = tempfile.mkdtemp(prefix="agent-rails-test-")
os.environ["AGENT_RAILS_STATE_DIR"] = _TMP

from agent_rails.core.engine import evaluate  # noqa: E402
from agent_rails.core.events import ERROR, OK, PENDING, ToolEvent, hash_args  # noqa: E402
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
    c = dict(BASE)
    c["mode"] = mode
    return c


def seed(session, n, tool="Bash", arg="a", status=OK):
    for _ in range(n):
        append_event(ToolEvent(session, tool, arg, status, 0.0))


def cand(session, tool="Bash", arg="a"):
    return ToolEvent(session, tool, arg, PENDING, 0.0)


def test_enforce_mode_blocks_repetition():
    s = "enforce-rep"
    seed(s, 3, arg="a")
    v = evaluate(s, cfg("enforce"), candidate=cand(s, arg="a"))
    assert v.action == BLOCK


def test_observe_mode_downgrades_block_to_nudge():
    s = "observe-rep"
    seed(s, 3, arg="a")
    v = evaluate(s, cfg("observe"), candidate=cand(s, arg="a"))
    assert v.action == NUDGE
    assert "WOULD BLOCK" in v.reason


def test_off_mode_always_allows():
    s = "off-rep"
    seed(s, 10, arg="a")
    v = evaluate(s, cfg("off"), candidate=cand(s, arg="a"))
    assert v.action == ALLOW


def test_highest_severity_wins():
    # both detectors fire; repetition BLOCK should beat error_streak NUDGE
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


if __name__ == "__main__":
    fns = {k: v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)}
    failed = 0
    for name, fn in fns.items():
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
