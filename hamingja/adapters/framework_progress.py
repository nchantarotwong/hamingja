"""Extract bounded failure counts from observed framework test output.

This adapter stores only counts keyed by the recorded command hash. Output and
command text never enter state. All parsing and persistence is fail-open.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path, PurePath

from ..config import load_config
from ..core.api import record_progress
from ..core.events import ToolEvent
from ..core.state import _lock, _state_dir, _unlock, read_recent
from .progress import _command_from

_MAX_OUTPUT = 65_536
_MAX_IDENTITIES = 64
_SHELL_META = (";", "|", "&", "`", "$", ">", "<", "\n", "\r")


def _framework(command: str) -> str:
    try:
        if not command or any(token in command for token in _SHELL_META):
            return ""
        parts = shlex.split(command)
        if not parts:
            return ""
        names = [PurePath(part).name.lower() for part in parts]
        if names[0] in {"pytest", "py.test"}:
            return "pytest"
        if len(names) >= 3 and names[0].startswith("python") and names[1:3] == ["-m", "pytest"]:
            return "pytest"
        if len(names) >= 3 and names[0].startswith("python") and names[1:3] == ["-m", "unittest"]:
            return "unittest"
        if names[:2] == ["cargo", "test"]:
            return "cargo"
        if names[0] in {"jest"} or names[:2] == ["npx", "jest"]:
            return "jest"
        if len(names) >= 2 and names[0] in {"npm", "yarn", "pnpm", "bun"} and names[1] == "test":
            return "jest"
        return ""
    except Exception:
        return ""


def _output(result: object) -> str:
    try:
        if isinstance(result, str):
            return result[-_MAX_OUTPUT:]
        if not isinstance(result, dict):
            return ""
        chunks = []
        for key in ("stdout", "stderr", "output"):
            value = result.get(key)
            if isinstance(value, str):
                chunks.append(value)
        return "\n".join(chunks)[-_MAX_OUTPUT:]
    except Exception:
        return ""


def parse_failure_count(framework: str, output: str) -> int | None:
    """Return a mechanically reported failure count, or None if ambiguous."""
    try:
        if not isinstance(output, str) or not output:
            return None
        if framework == "pytest":
            matches = re.findall(r"(?:^|[=, ])(\d+) failed(?:[, =]|$)", output, re.I | re.M)
            if matches:
                return int(matches[-1])
            if re.search(r"(?:^|[=, ])\d+ passed(?:[, =]|$)", output, re.I | re.M):
                return 0
        elif framework == "unittest":
            matches = re.findall(r"FAILED\s*\(([^)]*)\)", output, re.I)
            if matches:
                nums = re.findall(r"(?:failures|errors)\s*=\s*(\d+)", matches[-1], re.I)
                return sum(int(value) for value in nums) if nums else None
            if re.search(r"^OK(?:\s|$)", output, re.I | re.M):
                return 0
        elif framework == "cargo":
            matches = re.findall(r"test result:\s*(?:ok|FAILED)\..*?\b(\d+) failed\b", output, re.I)
            if matches:
                # A workspace can print one summary per test binary. Comparing
                # only the final binary could manufacture a false shrink.
                return sum(int(value) for value in matches)
        elif framework == "jest":
            matches = re.findall(r"^Tests:\s*(?:(\d+) failed,\s*)?(\d+) passed", output, re.I | re.M)
            if matches:
                return int(matches[-1][0] or 0)
        return None
    except Exception:
        return None


def _path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session_id))[:256]
    return _state_dir() / f"{safe or 'default'}-framework-progress.json"


def _update(session_id: str, identity: str, count: int) -> int | None:
    try:
        path = _path(session_id)
        with path.open("a+", encoding="utf-8") as fh:
            _lock(fh, exclusive=True)
            try:
                fh.seek(0)
                try:
                    data = json.loads(fh.read(131_073) or "{}")
                except Exception:
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                clean = {
                    str(key)[:160]: value for key, value in list(data.items())[-_MAX_IDENTITIES:]
                    if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)
                    and 0 <= value <= 1_000_000
                }
                previous = clean.pop(identity, None)
                clean[identity] = count
                clean = dict(list(clean.items())[-_MAX_IDENTITIES:])
                fh.seek(0)
                fh.truncate()
                fh.write(json.dumps(clean, sort_keys=True))
                fh.flush()
                return previous
            finally:
                _unlock(fh)
    except Exception:
        return None


def record_framework_progress(session_id: str, tool: str, tool_input: object,
                              result: object, project_dir=None, ok: bool | None = None) -> bool:
    """Measure a supported standalone test command and credit a proven shrink."""
    try:
        cfg = load_config(project_dir)
        budget_cfg = cfg.get("budget") if isinstance(cfg, dict) else None
        if cfg.get("mode") == "off" or not isinstance(budget_cfg, dict) or not budget_cfg.get("enabled", True):
            return False
        if str(tool) != "Bash":
            return False
        command = _command_from(tool_input).strip()
        framework = _framework(command)
        count = parse_failure_count(framework, _output(result))
        if not framework or count is None or count < 0 or count > 1_000_000:
            return False
        # A failed/interrupted run may contain an earlier passing summary. Only
        # the hook's explicit outcome can prove a zero-failure measurement.
        if count == 0 and ok is not True:
            return False
        events = read_recent(str(session_id), 1)
        candidate = ToolEvent.candidate(str(session_id), tool, tool_input)
        if not events or events[-1].arg_hash != candidate.arg_hash:
            return False
        identity = f"{framework}:{candidate.arg_hash}"
        previous = _update(str(session_id), identity, count)
        if previous is None or previous <= count:
            return False
        return record_progress(str(session_id), {
            "kind": "failure_set_shrank",
            "anchor": candidate.arg_hash,
            "validation_id": identity,
            "failure_count_before": previous,
            "failure_count_after": count,
        }, project_dir=project_dir)
    except Exception:
        return False
