"""Smoke test an installed wheel; run with its clean venv's Python and -I."""

import json
import os
from importlib import metadata, resources
from pathlib import Path
import subprocess
import sys
import tempfile

import hamingja


def main():
    # Do not let an editable/source import conceal an incomplete wheel.
    installed_path = Path(hamingja.__file__).resolve()
    if not installed_path.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError(f"Expected an installed wheel, imported {installed_path}")
    if metadata.version("hamingja") != hamingja.__version__:
        raise RuntimeError("Installed metadata and module versions differ")

    package = resources.files("hamingja")
    if not isinstance(json.loads(package.joinpath("config.default.json").read_text()), dict):
        raise RuntimeError("Default config must be a JSON object")
    for relative in (
        "adapters/claude_code/install.sh",
        "adapters/codex/install.sh",
        "profiles/base.md",
        "profiles/debugging.md",
        "profiles/escalation.md",
        "profiles/non_convergence.md",
        "profiles/read_discipline.md",
        "profiles/review_passes.md",
        "profiles/compiler_language.md",
        "templates/AGENTS.md",
        "templates/codex/AGENTS.md",
    ):
        if not package.joinpath(relative).read_text(encoding="utf-8").strip():
            raise RuntimeError(f"Empty packaged asset: {relative}")

    cli = Path(sys.executable).parent / "hamingja"
    subprocess.run([str(cli), "--version"], check=True, timeout=30)
    subprocess.run([str(cli), "--help"], check=True, timeout=30, stdout=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory(prefix="hamingja-install-smoke-") as temporary:
        directory = Path(temporary)
        settings = directory / "claude-settings.json"
        hooks = directory / "codex-hooks.json"
        environment = dict(os.environ, CLAUDE_SETTINGS=str(settings), CODEX_HOOKS=str(hooks))
        for arguments in (["status", temporary], ["init"], ["install", "all"]):
            subprocess.run(
                [str(cli), *arguments], cwd=directory, env=environment,
                check=True, timeout=30, stdout=subprocess.DEVNULL,
            )
        if not (directory / "CLAUDE.md").is_file() or not (directory / "AGENTS.md").is_file():
            raise RuntimeError("Installed init did not generate the instruction files")
        for path in (settings, hooks):
            if not json.loads(path.read_text())["hooks"].get("PreToolUse"):
                raise RuntimeError(f"Installer did not register pre-tool hooks: {path}")
        subprocess.run(
            [str(cli), "uninstall", "all"], cwd=directory, env=environment,
            check=True, timeout=30, stdout=subprocess.DEVNULL,
        )
        for path in (settings, hooks):
            if json.loads(path.read_text()).get("hooks"):
                raise RuntimeError(f"Uninstall left owned hooks: {path}")
    print(f"Installed wheel smoke passed: hamingja {hamingja.__version__}")


if __name__ == "__main__":
    main()
