"""Codex adapter tests - hook translation and installer behavior.

The recorder tests use representative payloads because Codex's docs leave
`tool_response` tool-specific and some shell paths do not emit PostToolUse.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_STATE_DIR = tempfile.mkdtemp(prefix="agent-rails-codex-test-")

from agent_rails.core.events import ERROR, OK, ToolEvent  # noqa: E402
from agent_rails.core.state import append_event, read_recent  # noqa: E402
from agent_rails.adapters.codex.tripwire import _is_budget_command  # noqa: E402
from agent_rails.ledger import add_record  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRIPWIRE = os.path.join(ROOT, "agent_rails", "adapters", "codex", "tripwire.py")
RECORDER = os.path.join(ROOT, "agent_rails", "adapters", "codex", "record.py")
INSTALL = os.path.join(ROOT, "agent_rails", "adapters", "codex", "install.sh")


def _proj(files=None):
    d = tempfile.mkdtemp(prefix="agent-rails-codex-proj-")
    os.mkdir(os.path.join(d, ".git"))
    for name, content in (files or {}).items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(content)
    return d


def _run_script(script, payload, env=None):
    e = os.environ.copy()
    e["AGENT_RAILS_STATE_DIR"] = _STATE_DIR
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, script],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=e,
        check=False,
    )


def _reset_state_env():
    os.environ["AGENT_RAILS_STATE_DIR"] = _STATE_DIR


def test_codex_record_marks_nonzero_exit_as_error():
    _reset_state_env()
    d = _proj()
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "codex-error",
        "cwd": d,
        "tool_name": "Bash",
        "tool_input": {"command": "false"},
        "tool_response": {"exit_code": 1, "stdout": "", "stderr": "boom"},
    }
    p = _run_script(RECORDER, payload)
    assert p.returncode == 0
    assert p.stdout == ""
    got = read_recent("codex-error", 5)
    assert len(got) == 1
    assert got[0].status == ERROR


def test_codex_record_marks_success_as_ok():
    _reset_state_env()
    d = _proj()
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "codex-ok",
        "cwd": d,
        "tool_name": "Bash",
        "tool_input": {"command": "true"},
        "tool_response": {"exit_code": 0, "stdout": "ok"},
    }
    p = _run_script(RECORDER, payload)
    assert p.returncode == 0
    got = read_recent("codex-ok", 5)
    assert len(got) == 1
    assert got[0].status == OK


def test_codex_tripwire_denies_in_enforce_after_repetition():
    _reset_state_env()
    d = _proj()
    args = {"command": "npm test"}
    for _ in range(3):
        append_event(ToolEvent.record("codex-deny", "Bash", args, False, output={"exit_code": 1, "stderr": "same failure"}))
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "codex-deny",
        "cwd": d,
        "tool_name": "Bash",
        "tool_input": args,
    }
    p = _run_script(TRIPWIRE, payload, {"AGENT_RAILS_MODE": "enforce"})
    assert p.returncode == 0
    out = json.loads(p.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "identical" in hso["permissionDecisionReason"]


def test_codex_tripwire_observe_downgrades_block_to_context():
    _reset_state_env()
    d = _proj()
    args = {"command": "npm test -- --watch=false"}
    for _ in range(3):
        append_event(ToolEvent.record("codex-observe", "Bash", args, False, output={"exit_code": 1, "stderr": "same failure"}))
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "codex-observe",
        "cwd": d,
        "tool_name": "Bash",
        "tool_input": args,
    }
    p = _run_script(TRIPWIRE, payload)
    assert p.returncode == 0
    out = json.loads(p.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in hso
    assert "WOULD BE BLOCKED" in hso["additionalContext"]


def test_codex_tripwire_nudges_agent_rails_wrapper_to_escalate():
    _reset_state_env()
    d = _proj()
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "codex-escalate-wrapper",
        "cwd": d,
        "tool_name": "Bash",
        "tool_input": {"command": "agent-rails ci-status 12"},
    }
    p = _run_script(TRIPWIRE, payload)
    assert p.returncode == 0
    out = json.loads(p.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in hso
    assert "sandbox_permissions" in hso["additionalContext"]
    assert "agent-rails ci-status" in hso["additionalContext"]


def test_codex_tripwire_skips_escalation_nudge_when_already_escalated():
    _reset_state_env()
    d = _proj()
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "codex-escalated-wrapper",
        "cwd": d,
        "tool_name": "Bash",
        "tool_input": {
            "command": "agent-rails ci-status 12",
            "sandbox_permissions": "require_escalated",
        },
    }
    p = _run_script(TRIPWIRE, payload)
    assert p.returncode == 0
    assert p.stdout == ""


def test_codex_tripwire_recognizes_timeout_wrapped_budget_command():
    assert _is_budget_command(
        "Bash",
        {"command": "timeout 30 agent-rails budget codex-session add 3 --self"},
    ) is True
    assert _is_budget_command(
        "Bash",
        {"command": "timeout 30 /usr/local/bin/agent-rails budget codex-session"},
    ) is True
    assert _is_budget_command("Bash", {"command": "timeout 30 agent-rails status"}) is False


def test_codex_tripwire_nudges_large_unscoped_read_to_locate():
    _reset_state_env()
    d = _proj({"large.py": "\n".join(f"line {i}" for i in range(250))})
    path = os.path.join(d, "large.py")
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "codex-large-read",
        "cwd": d,
        "tool_name": "Read",
        "tool_input": {"file_path": path},
    }
    p = _run_script(TRIPWIRE, payload)
    assert p.returncode == 0
    out = json.loads(p.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in hso
    assert "agent-rails locate" in hso["additionalContext"]
    assert "offset+limit" in hso["additionalContext"]


def test_codex_tripwire_no_large_read_nudge_for_scoped_read():
    _reset_state_env()
    d = _proj({"large.py": "\n".join(f"line {i}" for i in range(250))})
    path = os.path.join(d, "large.py")
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "codex-scoped-read",
        "cwd": d,
        "tool_name": "Read",
        "tool_input": {"file_path": path, "offset": 1, "limit": 50},
    }
    p = _run_script(TRIPWIRE, payload)
    assert p.returncode == 0
    assert p.stdout == ""


def test_codex_tripwire_surfaces_relevant_ledger_record_for_write():
    _reset_state_env()
    d = _proj({"x.py": "VALUE = 1\n"})
    result = add_record(
        Path(d),
        kind="constraint",
        claim="Do not edit VALUE to fix the parser",
        evidence="The parser test failed with VALUE unchanged.",
        scope=["x.py"],
    )
    assert result.ok
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "codex-ledger-write",
        "cwd": d,
        "tool_name": "Write",
        "tool_input": {"file_path": os.path.join(d, "x.py"), "content": "VALUE = 2\n"},
    }
    p = _run_script(TRIPWIRE, payload)
    assert p.returncode == 0
    out = json.loads(p.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in hso
    assert "Ruled-out ledger" in hso["additionalContext"]
    assert "Do not edit VALUE" in hso["additionalContext"]


def test_codex_bash_payload_variants_hash_distinct_commands():
    _reset_state_env()
    a = ToolEvent.record("codex-normalize", "Bash", {"parameters": {"cmd": "rg foo"}}, True)
    b = ToolEvent.record("codex-normalize", "Bash", {"arguments": {"command": "rg bar"}}, True)
    assert a.args_complete is True
    assert b.args_complete is True
    assert a.arg_hash != b.arg_hash
    assert a.arg_preview == "rg foo"
    assert b.arg_preview == "rg bar"


def test_codex_missing_bash_command_is_not_enforceable_repetition():
    _reset_state_env()
    d = _proj()
    args = {}
    for _ in range(5):
        append_event(ToolEvent.record("codex-missing", "Bash", args, True, output={"stdout": "same"}))
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "codex-missing",
        "cwd": d,
        "tool_name": "Bash",
        "tool_input": args,
    }
    p = _run_script(TRIPWIRE, payload, {"AGENT_RAILS_MODE": "enforce"})
    assert p.returncode == 0
    assert p.stdout == ""


def test_codex_install_merges_and_is_idempotent():
    d = tempfile.mkdtemp(prefix="agent-rails-codex-install-")
    hooks_path = os.path.join(d, "hooks.json")
    with open(hooks_path, "w", encoding="utf-8") as f:
        json.dump({
            "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "echo keep"}]}],
                "PreToolUse": [{
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "python /tmp/tripwire.py"}],
                }],
                "SubagentStart": [{
                    "matcher": "*",
                    "hooks": [{
                        "type": "command",
                        "command": "python /old/agent_rails/adapters/delegation.py",
                        "statusMessage": "Recording agent-rails subagent start",
                    }],
                }],
            }
        }, f)
    env = os.environ.copy()
    env["CODEX_HOOKS"] = hooks_path

    first = subprocess.run(["bash", INSTALL], text=True, capture_output=True, env=env)
    second = subprocess.run(["bash", INSTALL], text=True, capture_output=True, env=env)

    assert first.returncode == 0
    assert second.returncode == 0
    with open(hooks_path, encoding="utf-8") as f:
        cfg = json.load(f)
    assert "Stop" in cfg["hooks"]
    assert len(cfg["hooks"]["PreToolUse"]) == 2
    assert len(cfg["hooks"]["PostToolUse"]) == 1
    assert len(cfg["hooks"]["SubagentStart"]) == 1
    assert len(cfg["hooks"]["SubagentStop"]) == 1
    assert len(cfg["hooks"]["UserPromptSubmit"]) == 1
    assert "adapters/delegation.py" in cfg["hooks"]["SubagentStart"][0]["hooks"][0]["command"]
    assert "/old/" not in cfg["hooks"]["SubagentStart"][0]["hooks"][0]["command"]
    assert cfg["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "python /tmp/tripwire.py"
    assert "already up to date" in second.stdout


def test_codex_install_refuses_malformed_hooks():
    d = tempfile.mkdtemp(prefix="agent-rails-codex-malformed-")
    hooks_path = os.path.join(d, "hooks.json")
    with open(hooks_path, "w", encoding="utf-8") as f:
        f.write("not-json")
    env = os.environ.copy()
    env["CODEX_HOOKS"] = hooks_path
    result = subprocess.run(["bash", INSTALL], text=True, capture_output=True, env=env)
    assert result.returncode != 0
    assert "refusing to modify malformed" in result.stderr
    with open(hooks_path, encoding="utf-8") as f:
        assert f.read() == "not-json"
    assert not [name for name in os.listdir(d) if name.startswith("hooks.json.bak.")]


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
