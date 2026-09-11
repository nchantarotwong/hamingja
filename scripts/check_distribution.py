"""Smoke test an installed wheel; run with its clean venv's Python and -I."""

import argparse
import json
import os
from importlib import metadata, resources
from pathlib import Path
import subprocess
import sys
import tempfile

import hamingja


def main(release_tag=None):
    # Do not let an editable/source import conceal an incomplete wheel.
    installed_path = Path(hamingja.__file__).resolve()
    if not installed_path.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError(f"Expected an installed wheel, imported {installed_path}")
    if metadata.version("hamingja") != hamingja.__version__:
        raise RuntimeError("Installed metadata and module versions differ")
    if release_tag is not None and release_tag.removeprefix("v") != hamingja.__version__:
        raise RuntimeError(
            f"Release tag {release_tag!r} does not match installed version {hamingja.__version__}"
        )

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
    with tempfile.TemporaryDirectory(prefix="hamingja-install-smoke-") as temporary:
        directory = Path(temporary)
        # Bound project config discovery and keep operator state outside this check.
        (directory / ".git").mkdir()
        settings = directory / "claude-settings.json"
        hooks = directory / "codex-hooks.json"
        # -I is not inherited by CLI/installer subprocesses. Strip Python import
        # overrides as well as running the installed entry point in isolated mode.
        environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("PYTHON", "HAMINGJA_"))
        }
        environment.update(
            CLAUDE_SETTINGS=str(settings), CODEX_HOOKS=str(hooks),
            HAMINGJA_HOME=str(directory / "operator-config"),
            HAMINGJA_STATE_DIR=str(directory / "operator-state"),
        )
        command = [sys.executable, "-I", str(cli)]
        for arguments in (
            ["--version"], ["--help"], ["status", temporary], ["init"], ["install", "all"]
        ):
            subprocess.run(
                [*command, *arguments], cwd=directory, env=environment,
                check=True, timeout=30, stdout=subprocess.DEVNULL,
            )
        if not (directory / "CLAUDE.md").is_file() or not (directory / "AGENTS.md").is_file():
            raise RuntimeError("Installed init did not generate the instruction files")
        for path in (settings, hooks):
            if not json.loads(path.read_text())["hooks"].get("PreToolUse"):
                raise RuntimeError(f"Installer did not register pre-tool hooks: {path}")
        subprocess.run(
            [*command, "uninstall", "all"], cwd=directory, env=environment,
            check=True, timeout=30, stdout=subprocess.DEVNULL,
        )
        for path in (settings, hooks):
            if json.loads(path.read_text()).get("hooks"):
                raise RuntimeError(f"Uninstall left owned hooks: {path}")
    print(f"Installed wheel smoke passed: hamingja {hamingja.__version__}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", help="Require the installed version to match this release tag")
    main(release_tag=parser.parse_args().release_tag)
