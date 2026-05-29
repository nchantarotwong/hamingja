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
