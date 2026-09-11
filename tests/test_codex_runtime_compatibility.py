"""Synthetic hook-sequence coverage, not certification of runtime hook emission."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hamingja.core.events import ERROR, OK, ToolEvent
from hamingja.core.state import read_recent


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "hamingja" / "adapters" / "codex"


@pytest.fixture
def hook_session(tmp_path, monkeypatch):
    """Isolate all state/config; each hook invocation starts a new process."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".hamingja.json").write_text(
        json.dumps({"budget": {"enabled": False}}), encoding="utf-8"
    )
    monkeypatch.setenv("HAMINGJA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HAMINGJA_HOME", str(tmp_path / "trusted-config"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("HAMINGJA_MODE", "enforce")

    def payload(command, exit_code=0):
        return {
            "session_id": "synthetic-runtime-session",
            "turn_id": "synthetic-turn",
            "tool_use_id": f"synthetic-call-{command}",
            "cwd": str(project),
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"exit_code": exit_code},
        }

    def run(event, data):
        data = dict(data, hook_event_name=event)
        script = "tripwire.py" if event == "PreToolUse" else "record.py"
        if event == "PreToolUse":
            data.pop("tool_response")
        result = subprocess.run(
            [sys.executable, str(ADAPTER / script)],
            input=json.dumps(data), text=True, capture_output=True,
            env=os.environ.copy(), timeout=20, check=False,
        )
        assert result.returncode == 0, result.stderr
        if event == "PostToolUse":
            assert result.stdout == ""
        elif result.stdout:
            decision = json.loads(result.stdout)["hookSpecificOutput"]
            assert decision.get("permissionDecision") != "deny"
        return result

    return payload, run


def _expected(data):
    return ToolEvent.record(
        data["session_id"], data["tool_name"], data["tool_input"],
        data["tool_response"]["exit_code"] == 0, output=data["tool_response"],
    )


def test_parallel_recorders_preserve_every_distinct_outcome(hook_session):
    payload, run = hook_session
    calls = [payload(f"printf fixture-{index}", index % 2) for index in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda data: run("PostToolUse", data), calls))

    events = read_recent(calls[0]["session_id"], 20)
    assert len(events) == len(calls)
    assert {(e.arg_hash, e.status, e.output_hash) for e in events} == {
        (e.arg_hash, e.status, e.output_hash) for e in map(_expected, calls)
    }


def test_reversed_completion_order_keeps_payload_outcome_association(hook_session):
    payload, run = hook_session
    first = payload("printf slow-fixture", 1)
    second = payload("printf fast-fixture", 0)
    for data in (first, second):
        run("PreToolUse", data)
    for data in (second, first):
        run("PostToolUse", data)

    events = read_recent(first["session_id"], 20)
    assert [(e.arg_hash, e.status) for e in events] == [
        (_expected(second).arg_hash, OK), (_expected(first).arg_hash, ERROR),
    ]


def test_delayed_unified_exec_completion_records_original_bash_call(hook_session):
    payload, run = hook_session
    original = payload("printf delayed-fixture", 1)
    run("PreToolUse", original)
    # A yielded exec_command has not completed. Polling with write_stdin does
    # not deliver another PreToolUse; completion delivers the original Bash
    # payload. No transport event is synthesized for the adapter.
    assert read_recent(original["session_id"], 20) == []
    run("PostToolUse", original)
    events = read_recent(original["session_id"], 20)
    assert [(e.tool, e.arg_hash, e.status) for e in events] == [
        ("Bash", _expected(original).arg_hash, ERROR),
    ]


@pytest.mark.parametrize("tool,arguments,response,status", [
    ("mcp__fixture__read", {"path": "fixture.txt"},
     {"content": [{"type": "text", "text": "fixture"}], "isError": False}, OK),
    ("mcp__fixture__read", {"path": "missing.txt"},
     {"content": [{"type": "text", "text": "missing"}], "isError": True}, ERROR),
    ("update_plan", {"plan": [{"step": "fixture", "status": "completed"}]},
     "Plan updated", OK),
    ("apply_patch", {"command": "*** Begin Patch\n*** End Patch"},
     "No files were modified.", OK),
])
def test_mcp_and_local_function_response_shapes(
    hook_session, tool, arguments, response, status,
):
    payload, run = hook_session
    data = dict(payload("local-fixture"), tool_name=tool,
                tool_input=arguments, tool_response=response)
    run("PreToolUse", data)
    run("PostToolUse", data)
    events = read_recent(data["session_id"], 20)
    expected = ToolEvent.record(data["session_id"], tool, arguments,
                                status == OK, output=response)
    assert [(e.tool, e.arg_hash, e.status, e.output_hash) for e in events] == [
        (tool, expected.arg_hash, status, expected.output_hash),
    ]


def test_interrupted_call_without_post_hook_does_not_wedge_resume(hook_session):
    payload, run = hook_session
    interrupted = payload("printf interrupted-fixture")
    run("PreToolUse", interrupted)
    assert read_recent(interrupted["session_id"], 20) == []

    # The runtime never delivered the first result; a resumed hook process
    # must not invent an error, pending outcome, or denial from that absence.
    resumed = payload("printf resumed-fixture")
    run("PreToolUse", resumed)
    run("PostToolUse", resumed)
    events = read_recent(resumed["session_id"], 20)
    assert [(e.arg_hash, e.status) for e in events] == [
        (_expected(resumed).arg_hash, OK),
    ]
