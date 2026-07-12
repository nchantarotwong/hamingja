import io
import json

import agent_rails.adapters.delegation as adapter
from agent_rails.cli import main
from agent_rails.core.delegation import read_state, record_lifecycle


def _event(kind: str, agent_id: str, agent_type: str = "Explore") -> dict:
    return {
        "hook_event_name": kind,
        "session_id": "parent-session",
        "agent_id": agent_id,
        "agent_type": agent_type,
        "turn_id": "turn-1",
    }


def test_start_and_stop_track_active_children_by_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    first = record_lifecycle(_event("SubagentStart", "a1"))
    second = record_lifecycle(_event("SubagentStart", "a2", "review"))
    stopped = record_lifecycle(_event("SubagentStop", "a1"))
    assert first["active_children"] == 1
    assert second["active_children"] == 2
    assert stopped["active_children"] == 1
    assert stopped["started"] == 2
    assert stopped["completed"] == 1
    assert list(stopped["active"]) == ["a2"]


def test_duplicate_and_out_of_order_events_are_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    record_lifecycle(_event("SubagentStart", "a1"))
    record_lifecycle(_event("SubagentStart", "a1"))
    record_lifecycle(_event("SubagentStop", "missing"))
    state = read_state("parent-session")
    assert state["started"] == 1
    assert state["completed"] == 0
    assert state["active_children"] == 1


def test_malformed_lifecycle_fails_open(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    assert record_lifecycle(None) == {}
    assert record_lifecycle({"hook_event_name": "SubagentStart"}) == {}


def test_active_state_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    state = {}
    for index in range(70):
        state = record_lifecycle(_event("SubagentStart", f"a{index}"))
    assert state["active_children"] == 64
    assert len(state["active"]) == 64


def test_adapter_emits_advisory_above_active_limit(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(adapter, "load_config", lambda cwd=None: {
        "delegation": {"max_active_children": 1},
    })
    record_lifecycle(_event("SubagentStart", "a1"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_event("SubagentStart", "a2"))))
    assert adapter.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert "2 active children" in output["hookSpecificOutput"]["additionalContext"]
    assert output["systemMessage"] == output["hookSpecificOutput"]["additionalContext"]


def test_adapter_stop_returns_valid_empty_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_event("SubagentStop", "missing"))))
    assert adapter.main() == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_cli_exposes_bounded_delegation_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    record_lifecycle(_event("SubagentStart", "a1"))
    assert main(["delegation", "parent-session"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["active_children"] == 1
    assert output["active"]["a1"]["agent_type"] == "Explore"
    assert main(["delegation", "parent-session", "reset"]) == 0
    assert "state cleared" in capsys.readouterr().out
    assert read_state("parent-session") == {}


def test_oversized_state_read_fails_open(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    record_lifecycle(_event("SubagentStart", "a1"))
    path = next(tmp_path.glob("*-delegation.json"))
    path.write_text("{" + "x" * 200_000, encoding="utf-8")
    assert read_state("parent-session") == {}
