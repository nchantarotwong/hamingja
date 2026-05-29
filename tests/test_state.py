"""State store tests — round-trip, windowing, fail-open."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="agent-rails-test-")
os.environ["AGENT_RAILS_STATE_DIR"] = _TMP

from agent_rails.core.events import OK, ToolEvent  # noqa: E402
from agent_rails.core.state import append_event, read_recent  # noqa: E402


def test_round_trip_preserves_order():
    s = "rt"
    for i in range(5):
        append_event(ToolEvent(s, "Bash", f"h{i}", OK, float(i)))
    got = read_recent(s, 10)
    assert [e.arg_hash for e in got] == [f"h{i}" for i in range(5)]


def test_window_returns_most_recent():
    s = "win"
    for i in range(20):
        append_event(ToolEvent(s, "Bash", f"h{i}", OK, float(i)))
    got = read_recent(s, 3)
    assert [e.arg_hash for e in got] == ["h17", "h18", "h19"]


def test_missing_session_is_empty():
    assert read_recent("never-seen", 10) == []


def test_sessions_are_isolated():
    append_event(ToolEvent("sessA", "Bash", "a", OK, 0.0))
    append_event(ToolEvent("sessB", "Bash", "b", OK, 0.0))
    assert [e.arg_hash for e in read_recent("sessA", 10)] == ["a"]
    assert [e.arg_hash for e in read_recent("sessB", 10)] == ["b"]


def test_truncation_keeps_last_cap():
    s = "cap"
    for i in range(250):
        append_event(ToolEvent(s, "Bash", f"h{i}", OK, float(i)), cap=200)
    got = read_recent(s, 1000)
    assert len(got) == 200
    assert got[0].arg_hash == "h50"
    assert got[-1].arg_hash == "h249"


def test_nonpositive_window_does_not_return_everything():
    # window<=0 used to hit the lines[-0:] == lines[:] quirk; guard clamps to 1
    s = "zerowin"
    for i in range(5):
        append_event(ToolEvent(s, "Bash", f"h{i}", OK, float(i)))
    assert len(read_recent(s, 0)) == 1


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
