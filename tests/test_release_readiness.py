import ast
import re
from pathlib import Path

import agent_rails


ROOT = Path(__file__).resolve().parents[1]


def test_declared_python_floor_matches_test_runtime_and_parses_sources():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.13"' in pyproject
    for path in (ROOT / "agent_rails").rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 13))


def test_release_metadata_has_real_urls_and_single_version_source():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in pyproject
    assert 'version = {attr = "agent_rails.__version__"}' in pyproject
    assert "https://github.com/nchantarotwong/agent-rails" in pyproject
    assert 'license = "Apache-2.0"' in pyproject
    assert re.fullmatch(r"\d+\.\d+\.\d+", agent_rails.__version__)


def test_sdist_manifest_carries_release_and_runtime_assets():
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    for required in (
        "LICENSE", "NOTICE", "README.md", "CHANGELOG.md",
        "agent_rails/config.default.json",
        "agent_rails/adapters/claude_code/install.sh",
        "agent_rails/adapters/codex/install.sh",
    ):
        assert required in manifest
