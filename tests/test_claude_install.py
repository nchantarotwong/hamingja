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
    }}), encoding="utf-8")
    env = os.environ.copy()
    env["CLAUDE_SETTINGS"] = str(settings)
    first = subprocess.run(["bash", str(INSTALL)], text=True, capture_output=True, env=env)
    second = subprocess.run(["bash", str(INSTALL)], text=True, capture_output=True, env=env)
    assert first.returncode == second.returncode == 0
    cfg = json.loads(settings.read_text(encoding="utf-8"))
    assert "Stop" in cfg["hooks"]
    for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure", "SubagentStart", "SubagentStop", "UserPromptSubmit"):
        assert len(cfg["hooks"][event]) == 1
    assert "adapters/delegation.py" in cfg["hooks"]["SubagentStop"][0]["hooks"][0]["command"]
    assert "already up to date" in second.stdout
