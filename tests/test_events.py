"""Event normalization tests.

These guard the enforcement boundary: if adapter payloads are incomplete or
output fingerprints are too broad, repetition can become noisy or unsafe.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_rails.core.events import ToolEvent, hash_output  # noqa: E402


def test_bash_payload_variants_normalize_to_command_identity():
    a = ToolEvent.record("s", "Bash", {"parameters": {"cmd": "rg foo"}}, True)
    b = ToolEvent.record("s", "Bash", {"arguments": {"command": "rg bar"}}, True)
    assert a.args_complete is True
    assert b.args_complete is True
    assert a.arg_hash != b.arg_hash
    assert a.arg_preview == "rg foo"
    assert b.arg_preview == "rg bar"


def test_missing_bash_command_is_incomplete():
    e = ToolEvent.candidate("s", "Bash", {})
    assert e.args_complete is False
    assert e.arg_kind == "shell:missing-command"
    assert e.arg_preview == "<missing Bash command>"


def test_shell_command_kind_classification():
    read = ToolEvent.candidate("s", "Bash", {"command": "rg foo"})
    test = ToolEvent.candidate("s", "Bash", {"command": "python -m pytest"})
    mutate = ToolEvent.candidate("s", "Bash", {"command": "git add file.py"})
    assert read.arg_kind == "shell:read-only"
    assert test.arg_kind == "shell:test"
    assert mutate.arg_kind == "shell:mutating"


def test_command_preview_redacts_obvious_secrets():
    e = ToolEvent.candidate("s", "Bash", {"command": "curl -H 'Authorization: Bearer abc123' https://x.test?api_key=secret"})
    assert "abc123" not in e.arg_preview
    assert "api_key=secret" not in e.arg_preview
    assert "<redacted>" in e.arg_preview


def test_output_hash_ignores_status_only_success():
    assert hash_output({"exit_code": 0, "stdout": "", "stderr": ""}) == ""


def test_output_hash_uses_substantive_text_and_status():
    a = hash_output({"exit_code": 0, "stdout": "same"})
    b = hash_output({"exit_code": 1, "stdout": "same"})
    assert a
    assert b
    assert a != b


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _run import run_module_tests
    sys.exit(run_module_tests(globals()))
