import io
import json

import hamingja.adapters.operator_turn as adapter
from hamingja.core.budget import read_state


def test_operator_hook_marks_anchor_without_storing_prompt(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HAMINGJA_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(adapter, "load_config", lambda cwd=None: {
        "budget": {"enabled": True, "checkpoint_at": 12},
    })
    secret = "private operator prompt"
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "operator-session",
        "cwd": str(tmp_path),
        "prompt": secret,
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert adapter.main() == 0
    assert json.loads(capsys.readouterr().out) == {}
    state = read_state("operator-session")
    assert state["operator_turn_observed"] is True
    assert state["weighted_at_last_operator"] == 0.0
    assert secret not in next(tmp_path.glob("*-budget.json")).read_text(encoding="utf-8")


def test_operator_hook_malformed_payload_fails_open(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HAMINGJA_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO("not-json"))
    assert adapter.main() == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_operator_hook_mode_off_is_fully_inert(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HAMINGJA_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(adapter, "load_config", lambda cwd=None: {
        "mode": "off", "budget": {"enabled": True, "checkpoint_at": 12},
    })
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "off-session",
        "prompt": "must not persist",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert adapter.main() == 0
    assert json.loads(capsys.readouterr().out) == {}
    assert read_state("off-session") == {}
