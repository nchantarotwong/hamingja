import json

from hamingja.adapters.progress import record_workflow_progress
from hamingja.core import budget
from hamingja.core.api import record


def _seed(session_id: str):
    cfg = {"checkpoint_at": 12, "hard_block_at": 20, "nudge_at": 8, "poll_timeout_s": 0}
    for _ in range(15):
        budget.increment_and_check(session_id, "Bash", False, cfg)


def test_ready_ci_lifecycle_credits_anchored_workflow_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("HAMINGJA_STATE_DIR", str(tmp_path))
    sid = "adapter-ci-ready"
    _seed(sid)
    args = {"command": "hamingja ci-status 12 --json"}
    result = {"stdout": json.dumps({
        "schema_version": 1,
        "operation": "ci_status",
        "state": "ready",
        "exit_code": 0,
    })}
    record(sid, "Bash", args, True, project_dir=str(tmp_path), output=result)
    before = budget.read_state(sid)["weighted_calls"]
    assert record_workflow_progress(sid, "Bash", args, result, project_dir=str(tmp_path)) is True
    state = budget.read_state(sid)
    assert state["weighted_calls"] < before
    assert state["last_progress"]["state_after"] == "ready"


def test_pending_or_malformed_lifecycle_earns_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HAMINGJA_STATE_DIR", str(tmp_path))
    sid = "adapter-not-progress"
    _seed(sid)
    args = {"command": "hamingja ci-status 12 --json"}
    record(sid, "Bash", args, True, project_dir=str(tmp_path), output={"stdout": "pending"})
    before = budget.read_state(sid)["weighted_calls"]
    assert record_workflow_progress(sid, "Bash", args, {"stdout": "not-json"}, str(tmp_path)) is False
    assert record_workflow_progress(sid, "Bash", args, {"stdout": json.dumps({
        "schema_version": 1, "operation": "ci_status", "state": "pending",
    })}, str(tmp_path)) is False
    assert budget.read_state(sid)["weighted_calls"] == before


def test_arbitrary_command_cannot_farm_lifecycle_credit(tmp_path, monkeypatch):
    monkeypatch.setenv("HAMINGJA_STATE_DIR", str(tmp_path))
    sid = "adapter-fake-progress"
    _seed(sid)
    args = {"command": "echo '{\"operation\":\"pr_merge\",\"state\":\"merged\"}'"}
    result = {"stdout": json.dumps({
        "schema_version": 1, "operation": "pr_merge", "state": "merged",
    })}
    record(sid, "Bash", args, True, project_dir=str(tmp_path), output=result)
    before = budget.read_state(sid)["weighted_calls"]
    assert record_workflow_progress(sid, "Bash", args, result, str(tmp_path)) is False
    assert budget.read_state(sid)["weighted_calls"] == before


def test_wrapper_pipeline_cannot_farm_lifecycle_credit(tmp_path, monkeypatch):
    monkeypatch.setenv("HAMINGJA_STATE_DIR", str(tmp_path))
    sid = "adapter-pipeline-progress"
    _seed(sid)
    args = {"command": "hamingja pr-merge 12 --json || echo fake"}
    result = {"stdout": json.dumps({
        "schema_version": 1, "operation": "pr_merge", "state": "merged",
    })}
    record(sid, "Bash", args, True, project_dir=str(tmp_path), output=result)
    assert record_workflow_progress(sid, "Bash", args, result, str(tmp_path)) is False


def test_arbitrary_executable_prefix_cannot_spoof_wrapper(tmp_path, monkeypatch):
    monkeypatch.setenv("HAMINGJA_STATE_DIR", str(tmp_path))
    sid = "adapter-prefix-progress"
    _seed(sid)
    args = {"command": "fake-runner hamingja pr-merge 12 --json"}
    result = {"stdout": json.dumps({
        "schema_version": 1, "operation": "pr_merge", "state": "merged",
    })}
    record(sid, "Bash", args, True, project_dir=str(tmp_path), output=result)
    assert record_workflow_progress(sid, "Bash", args, result, str(tmp_path)) is False


def test_stale_previous_event_cannot_anchor_current_wrapper(tmp_path, monkeypatch):
    monkeypatch.setenv("HAMINGJA_STATE_DIR", str(tmp_path))
    sid = "adapter-stale-anchor"
    _seed(sid)
    record(sid, "Bash", {"command": "echo prior"}, True, project_dir=str(tmp_path))
    args = {"command": "hamingja ci-status 12 --json"}
    result = {"stdout": json.dumps({
        "schema_version": 1, "operation": "ci_status", "state": "ready",
    })}
    assert record_workflow_progress(sid, "Bash", args, result, str(tmp_path)) is False
