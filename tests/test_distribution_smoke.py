"""Exercise the release checker without touching installed hook configuration."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def checker(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "distribution_checker", ROOT / "scripts/check_distribution.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.hamingja, "__file__", str(Path(sys.prefix) / "hamingja/__init__.py"))
    monkeypatch.setattr(module.metadata, "version", lambda name: module.hamingja.__version__)
    return module


@pytest.mark.parametrize("tag", ["v9.99.999", "", "v0.1.1; echo injected"])
def test_release_tag_mismatch_rejected_before_any_cli_execution(checker, monkeypatch, tag):
    def unexpected_run(*args, **kwargs):
        pytest.fail("A mismatched release must fail before running the CLI")

    monkeypatch.setattr(checker.subprocess, "run", unexpected_run)
    with pytest.raises(RuntimeError, match="Release tag"):
        checker.main(release_tag=tag)


@pytest.mark.parametrize("tag_prefix", [None, "", "v"])
def test_smoke_children_ignore_source_import_and_operator_environment(
    checker, monkeypatch, tmp_path, tag_prefix
):
    # A source-only module must remain invisible even to installer subprocesses.
    (tmp_path / "source_contamination_probe.py").write_text("ORIGIN = 'checkout'\n")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("HAMINGJA_HOME", str(tmp_path / "operator-config"))
    monkeypatch.setenv("HAMINGJA_STATE_DIR", str(tmp_path / "operator-state"))
    real_run = subprocess.run
    calls = []

    def simulate_cli(command, **kwargs):
        environment = kwargs.get("env", os.environ)
        probe = real_run(
            [sys.executable, "-c", "import importlib.util; assert importlib.util.find_spec('source_contamination_probe') is None"],
            cwd=tmp_path.parent, env=environment, capture_output=True, text=True,
        )
        assert probe.returncode == 0, probe.stderr
        assert environment["HAMINGJA_HOME"] != str(tmp_path / "operator-config")
        assert environment["HAMINGJA_STATE_DIR"] != str(tmp_path / "operator-state")
        directory = Path(kwargs["cwd"])
        assert (directory / ".git").is_dir()
        calls.append(command)
        if command[-1] == "init":
            (directory / "CLAUDE.md").write_text("synthetic managed instructions")
            (directory / "AGENTS.md").symlink_to("CLAUDE.md")
        if command[-2:] in (["install", "all"], ["uninstall", "all"]):
            hooks = {"PreToolUse": [{"hooks": []}]} if command[-2] == "install" else {}
            for key in ("CLAUDE_SETTINGS", "CODEX_HOOKS"):
                Path(environment[key]).write_text(json.dumps({"hooks": hooks}))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(checker.subprocess, "run", simulate_cli)
    if tag_prefix is None:
        checker.main()
    else:
        checker.main(release_tag=tag_prefix + checker.hamingja.__version__)
    assert len(calls) == 6
