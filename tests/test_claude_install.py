import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "hamingja" / "adapters" / "claude_code" / "install.sh"


def test_claude_install_registers_lifecycle_hooks_idempotently(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "echo keep"}]}],
        "PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "python /tmp/tripwire.py"}],
        }],
        "PostToolUse": [{
            "matcher": "*",
            "hooks": [{
                "type": "command",
                "command": "python /old/agent_rails/adapters/claude_code/record.py",
            }],
        }],
    }}), encoding="utf-8")
    settings.chmod(0o640)
    env = os.environ.copy()
    env["CLAUDE_SETTINGS"] = str(settings)
    first = subprocess.run(["bash", str(INSTALL)], text=True, capture_output=True, env=env)
    second = subprocess.run(["bash", str(INSTALL)], text=True, capture_output=True, env=env)
    assert first.returncode == second.returncode == 0
    cfg = json.loads(settings.read_text(encoding="utf-8"))
    assert "Stop" in cfg["hooks"]
    assert settings.stat().st_mode & 0o777 == 0o640
    assert len(cfg["hooks"]["PreToolUse"]) == 2
    assert cfg["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "python /tmp/tripwire.py"
    assert len(cfg["hooks"]["PostToolUse"]) == 1
    assert "/old/" not in cfg["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    for event in ("PostToolUse", "PostToolUseFailure", "SubagentStart", "SubagentStop", "UserPromptSubmit"):
        assert len(cfg["hooks"][event]) == 1
    assert "adapters/delegation.py" in cfg["hooks"]["SubagentStop"][0]["hooks"][0]["command"]
    assert "already up to date" in second.stdout


def test_claude_install_refuses_malformed_settings(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("not-json", encoding="utf-8")
    env = os.environ.copy()
    env["CLAUDE_SETTINGS"] = str(settings)
    result = subprocess.run(["bash", str(INSTALL)], text=True, capture_output=True, env=env)
    assert result.returncode != 0
    assert "refusing to modify malformed" in result.stderr
    assert settings.read_text(encoding="utf-8") == "not-json"
    assert not list(tmp_path.glob("settings.json.bak.*"))


def test_claude_install_refuses_malformed_managed_event(tmp_path):
    settings = tmp_path / "settings.json"
    original = json.dumps({"hooks": {"PreToolUse": {"unexpected": True}}})
    settings.write_text(original, encoding="utf-8")
    env = os.environ.copy()
    env["CLAUDE_SETTINGS"] = str(settings)
    result = subprocess.run(["bash", str(INSTALL)], text=True, capture_output=True, env=env)
    assert result.returncode != 0
    assert "refusing to replace malformed PreToolUse" in result.stderr
    assert settings.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("settings.json.bak.*"))


def test_claude_install_preserves_symlinked_settings(tmp_path):
    target = tmp_path / "dotfiles" / "settings.json"
    target.parent.mkdir()
    target.write_text("{}", encoding="utf-8")
    settings = tmp_path / "settings.json"
    settings.symlink_to(target)
    env = os.environ.copy()
    env["CLAUDE_SETTINGS"] = str(settings)
    result = subprocess.run(["bash", str(INSTALL)], text=True, capture_output=True, env=env)
    assert result.returncode == 0
    assert settings.is_symlink()
    assert "PreToolUse" in json.loads(target.read_text(encoding="utf-8"))["hooks"]


def test_claude_install_honors_explicit_python(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    env = os.environ.copy()
    env["CLAUDE_SETTINGS"] = str(settings)
    env["HAMINGJA_PYTHON"] = sys.executable
    result = subprocess.run(["bash", str(INSTALL)], text=True, capture_output=True, env=env)
    assert result.returncode == 0
    command = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert command.startswith(f'"{sys.executable}" ')
