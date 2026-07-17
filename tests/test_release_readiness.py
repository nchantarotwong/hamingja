import ast
import re
from pathlib import Path

import hamingja


ROOT = Path(__file__).resolve().parents[1]


def test_declared_python_floor_matches_test_runtime_and_parses_sources():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.13"' in pyproject
    for path in (ROOT / "hamingja").rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 13))


def test_release_metadata_has_real_urls_and_single_version_source():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in pyproject
    assert 'version = {attr = "hamingja.__version__"}' in pyproject
    assert "https://github.com/nchantarotwong/hamingja" in pyproject
    assert 'license = "Apache-2.0"' in pyproject
    assert re.fullmatch(r"\d+\.\d+\.\d+", hamingja.__version__)


def test_sdist_manifest_carries_release_and_runtime_assets():
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    for required in (
        "LICENSE", "NOTICE", "README.md", "CHANGELOG.md",
        "hamingja/config.default.json",
        "hamingja/adapters/claude_code/install.sh",
        "hamingja/adapters/codex/install.sh",
    ):
        assert required in manifest


def test_release_workflow_is_bounded_to_trusted_publishing():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "release:\n    types: [published]" in workflow
    assert "push:" not in workflow
    assert "pull_request:" not in workflow
    assert "workflow_dispatch:" not in workflow
    assert "environment:\n      name: pypi" in workflow
    assert workflow.count("id-token: write") == 1
    assert "ref: ${{ github.event.release.tag_name }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "python -m build" in workflow
    assert "pypa/gh-action-pypi-publish@" in workflow

    build_job, publish_job = workflow.split("\n  publish:", maxsplit=1)
    assert "id-token: write" not in build_job
    assert "needs: build" in publish_job
    assert "contents: read" not in publish_job
    assert workflow.count("name: python-package-distributions") == 2

    action_refs = re.findall(r"uses: [^@\s]+@([^\s]+)", workflow)
    assert len(action_refs) == 5
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)
