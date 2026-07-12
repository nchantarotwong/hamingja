import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "agent_rails" / "adapters" / "claude_code" / "install.sh"


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
    env = os.environ.copy()
    env["CLAUDE_SETTINGS"] = str(settings)
    first = subprocess.run(["bash", str(INSTALL)], text=True, capture_output=True, env=env)
    second = subprocess.run(["bash", str(INSTALL)], text=True, capture_output=True, env=env)
    assert first.returncode == second.returncode == 0
    cfg = json.loads(settings.read_text(encoding="utf-8"))
    assert "Stop" in cfg["hooks"]
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
