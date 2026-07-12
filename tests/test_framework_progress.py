import json

import pytest

from agent_rails.adapters.framework_progress import parse_failure_count, record_framework_progress
from agent_rails.core import budget
from agent_rails.core.api import record


@pytest.mark.parametrize(("framework", "output", "expected"), [
    ("pytest", "===== 3 failed, 8 passed in 1.0s =====", 3),
    ("pytest", "===== 11 passed in 1.0s =====", 0),
    ("unittest", "FAILED (failures=2, errors=1)", 3),
    ("unittest", "Ran 4 tests\n\nOK\n", 0),
    ("cargo", "test result: FAILED. 7 passed; 2 failed; 0 ignored", 2),
    ("cargo", "test result: ok. 7 passed; 0 failed; 0 ignored", 0),
    ("cargo", "test result: FAILED. 1 passed; 2 failed; 0 ignored\n"
              "test result: FAILED. 3 passed; 1 failed; 0 ignored", 3),
    ("jest", "Tests:       2 failed, 8 passed, 10 total", 2),
    ("jest", "Tests:       8 passed, 8 total", 0),
])
def test_parses_framework_summary(framework, output, expected):
    assert parse_failure_count(framework, output) == expected


def _seed(session_id):
    cfg = {"checkpoint_at": 12, "hard_block_at": 20, "nudge_at": 8, "poll_timeout_s": 0}
    for _ in range(15):
        budget.increment_and_check(session_id, "Bash", False, cfg)


def _observe(tmp_path, sid, command, output, ok=False):
    args = {"command": command}
    result = {"stdout": output, "exit_code": 0 if ok else 1}
    record(sid, "Bash", args, ok, project_dir=str(tmp_path), output=result)
    return record_framework_progress(sid, "Bash", args, result, str(tmp_path), ok=ok)


def test_same_validation_failure_set_shrink_credits_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    sid = "pytest-shrink"
    _seed(sid)
    assert _observe(tmp_path, sid, "python -m pytest -q", "5 failed, 2 passed") is False
    before = budget.read_state(sid)["weighted_calls"]
    assert _observe(tmp_path, sid, "python -m pytest -q", "2 failed, 5 passed") is True
    state = budget.read_state(sid)
    assert state["weighted_calls"] < before
    assert state["last_progress"]["failure_count_before"] == 5
    assert state["last_progress"]["failure_count_after"] == 2


def test_equal_increased_and_different_command_earn_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    sid = "no-shrink"
    _seed(sid)
    assert not _observe(tmp_path, sid, "pytest -q", "2 failed, 5 passed")
    assert not _observe(tmp_path, sid, "pytest -q", "3 failed, 4 passed")
    assert not _observe(tmp_path, sid, "pytest -q tests/unit", "1 failed, 3 passed")


def test_failed_run_cannot_turn_passing_text_into_zero_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    sid = "false-zero"
    _seed(sid)
    assert not _observe(tmp_path, sid, "pytest -q", "2 failed, 5 passed")
    before = budget.read_state(sid)["weighted_calls"]
    assert not _observe(tmp_path, sid, "pytest -q", "5 passed\ninterrupted", ok=False)
    assert budget.read_state(sid)["weighted_calls"] == before


def test_successful_run_can_complete_failure_set_shrink_to_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    sid = "shrink-zero"
    _seed(sid)
    assert not _observe(tmp_path, sid, "pytest -q", "2 failed, 5 passed")
    before = budget.read_state(sid)["weighted_calls"]
    # Core red->green is stronger and credits first; the adapter must not pay a
    # second credit, but it still retains the current count for later runs.
    assert not _observe(tmp_path, sid, "pytest -q", "7 passed", ok=True)
    assert budget.read_state(sid)["weighted_calls"] < before
    measurements = json.loads((tmp_path / f"{sid}-framework-progress.json").read_text())
    assert list(measurements.values()) == [0]


@pytest.mark.parametrize("command", [
    "pytest -q | tee output.log",
    "pytest -q; echo '1 passed'",
    "echo '1 failed'",
    "fake-runner pytest -q",
])
def test_composed_or_spoofed_commands_are_not_measured(tmp_path, monkeypatch, command):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    sid = "spoof"
    _seed(sid)
    assert not _observe(tmp_path, sid, command, "1 failed, 9 passed")
    assert not list(tmp_path.glob("*-framework-progress.json"))


def test_stale_event_and_malformed_output_fail_open(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    sid = "stale"
    record(sid, "Bash", {"command": "echo prior"}, True, str(tmp_path))
    args = {"command": "cargo test"}
    assert not record_framework_progress(sid, "Bash", args, None, str(tmp_path))
    assert not record_framework_progress(sid, "Bash", args, {"stdout": "not a summary"}, str(tmp_path))


def test_mode_off_is_inert(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    (tmp_path / ".agent-rails-off").write_text("")
    assert not _observe(tmp_path, "off", "pytest -q", "3 failed, 1 passed")
    assert not list(tmp_path.glob("*-framework-progress.json"))


def test_corrupt_state_is_replaced_without_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RAILS_STATE_DIR", str(tmp_path))
    sid = "corrupt"
    path = tmp_path / f"{sid}-framework-progress.json"
    path.write_text("not-json")
    assert not _observe(tmp_path, sid, "npm test", "Tests: 2 failed, 3 passed, 5 total")
    assert isinstance(json.loads(path.read_text()), dict)
