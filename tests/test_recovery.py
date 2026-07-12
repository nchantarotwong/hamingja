from hamingja.core.events import ToolEvent
from hamingja.core.state import append_event, read_recent, reset_session


def test_reset_session_clears_only_named_detector_history(tmp_path, monkeypatch):
    monkeypatch.setenv("HAMINGJA_STATE_DIR", str(tmp_path))
    append_event(ToolEvent.record("one", "Read", {"file_path": "a"}, True))
    append_event(ToolEvent.record("two", "Read", {"file_path": "b"}, True))
    assert reset_session("one") is True
    assert read_recent("one", 4) == []
    assert len(read_recent("two", 4)) == 1


def test_reset_session_missing_or_bad_state_fails_open(tmp_path, monkeypatch):
    monkeypatch.setenv("HAMINGJA_STATE_DIR", str(tmp_path))
    assert reset_session("missing") is False
    monkeypatch.setenv("HAMINGJA_STATE_DIR", str(tmp_path / "not-a-dir"))
    (tmp_path / "not-a-dir").write_text("x", encoding="utf-8")
    assert reset_session("broken") is False
