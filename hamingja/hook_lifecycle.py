"""Preserving hook-config discovery and uninstall lifecycle."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path


HARNESS_ALIASES = {"claude": "claude_code"}
KNOWN_HARNESSES = ["claude_code", "codex"]
HARNESS_HOMES = {".claude": "claude_code", ".codex": "codex"}
_HOOK_SCRIPTS = {"tripwire.py", "record.py", "delegation.py", "operator_turn.py"}
_OWNED_ADAPTER_PATHS = ("hamingja/adapters/", "agent_rails/adapters/")


def detect_harnesses(home: Path) -> list[str]:
    """Return harnesses whose config directory exists, in stable order."""
    found: list[str] = []
    for directory, harness in HARNESS_HOMES.items():
        try:
            if (home / directory).is_dir():
                found.append(harness)
        except OSError:
            continue
    return found


def _is_hamingja_hook(value: object) -> bool:
    try:
        if not isinstance(value, dict):
            return False
        command = str(value.get("command", "")).replace("\\", "/")
        try:
            parts = shlex.split(command)
        except ValueError:
            return False
        return any(path in command for path in _OWNED_ADAPTER_PATHS) and any(
            Path(part).name in _HOOK_SCRIPTS for part in parts
        )
    except Exception:
        return False


def _config_path(harness: str) -> Path:
    if harness == "claude_code":
        override = os.environ.get("CLAUDE_SETTINGS")
        return Path(override) if override else Path.home() / ".claude" / "settings.json"
    override = os.environ.get("CODEX_HOOKS")
    return Path(override) if override else Path.home() / ".codex" / "hooks.json"


def _read_config(path: Path) -> tuple[dict, dict] | None:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("top-level config is not an object")
    hooks = config.get("hooks")
    if hooks is None:
        return None
    if not isinstance(hooks, dict):
        raise ValueError("hooks is not an object")
    return config, hooks


def _remove_owned_hooks(hooks: dict) -> bool:
    changed = False
    for event in list(hooks):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        kept_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                kept_entries.append(entry)
                continue
            commands = entry.get("hooks")
            if not isinstance(commands, list):
                kept_entries.append(entry)
                continue
            kept_commands = [item for item in commands if not _is_hamingja_hook(item)]
            changed |= len(kept_commands) != len(commands)
            if kept_commands:
                clean_entry = dict(entry)
                clean_entry["hooks"] = kept_commands
                kept_entries.append(clean_entry)
        if kept_entries:
            hooks[event] = kept_entries
        elif entries:
            hooks.pop(event, None)
    return changed


def _atomic_write(path: Path, config: dict) -> None:
    write_path = path.resolve() if path.is_symlink() else path
    fd, name = tempfile.mkstemp(prefix=".hamingja-uninstall-", dir=write_path.parent)
    temporary = Path(name)
    try:
        os.fchmod(fd, write_path.stat().st_mode & 0o7777)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, write_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def uninstall_one(harness: str) -> int:
    path = _config_path(harness)
    if not path.exists():
        print(f"no change: {path} does not exist")
        return 0
    try:
        loaded = _read_config(path)
    except Exception as exc:
        print(
            f"error: refusing to modify malformed hook config {path}: {exc}",
            file=sys.stderr,
        )
        return 1
    if loaded is None:
        print(f"no change: {path} has no hooks")
        return 0
    config, hooks = loaded
    if not _remove_owned_hooks(hooks):
        print(f"no change: {path} has no hamingja hooks")
        return 0

    backup = Path(f"{path}.bak.uninstall.{int(time.time())}.{os.getpid()}")
    try:
        shutil.copy2(path, backup)
        _atomic_write(path, config)
    except Exception as exc:
        detail = f"; backup preserved at {backup}" if backup.exists() else ""
        print(f"error: unable to update hook config {path}: {exc}{detail}", file=sys.stderr)
        return 1
    print(f"updated:  {path}")
    print(f"backup:   {backup}")
    return 0


def uninstall(harness: str | None) -> int:
    if harness is None:
        targets = detect_harnesses(Path.home())
        if not targets:
            print("no installed harness config detected")
            return 0
    elif harness == "all":
        targets = list(KNOWN_HARNESSES)
    else:
        targets = [HARNESS_ALIASES.get(harness, harness)]
    result = 0
    for index, target in enumerate(targets):
        if len(targets) > 1:
            if index:
                print()
            print(f"--- uninstalling for {target} ---")
        result = max(result, uninstall_one(target))
    return result
