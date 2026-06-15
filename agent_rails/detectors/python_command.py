"""PythonCommandDetector — nudge `python` Bash calls toward python3 / repo venv.

On macOS (and many Linux distros) ``python`` is not aliased to ``python3``, so
a Bash invocation that starts with ``python `` fails with "command not found"
or, worse, finds an unintended interpreter. The model then retries and burns
tool calls. A nudge on the first try costs nothing and shortcuts the loop.

NUDGE-ONLY (fail-open): this is a heuristic on a command pattern. The actual
guarantee is the shell. We just feed back better wording.

Design:
  * Only fires on Bash events whose command's first whitespace-delimited token
    is exactly ``python``. ``python3``, ``python3.11``, ``pythonsomething`` are
    not affected.
  * Skips when the command already runs the repo venv interpreter directly
    (path contains ``.venv/bin/python``) — the model already did the right
    thing.
  * If the current working directory contains a ``.venv/bin/python``, the
    nudge names that exact path; otherwise it falls back to suggesting
    ``python3``.
  * Single-fire per candidate: we don't accumulate history, so noisy logs of
    past mistakes don't matter — only the call about to run does.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .base import NUDGE, Detector, Verdict

if TYPE_CHECKING:
    from ..core.events import ToolEvent


def _first_token(s: str) -> str:
    s = (s or "").lstrip()
    if not s:
        return ""
    cut = len(s)
    for i, ch in enumerate(s):
        if ch.isspace():
            cut = i
            break
    return s[:cut]


def _repo_venv_python() -> Optional[str]:
    """Return a repo-local .venv/bin/python path if one exists at cwd, else None."""
    try:
        candidate = Path(os.getcwd()) / ".venv" / "bin" / "python"
        if candidate.exists():
            return str(candidate)
    except OSError:
        return None
    return None


class PythonCommandDetector(Detector):
    name = "python_command"

    def evaluate(
        self,
        events: list["ToolEvent"],
        candidate: Optional["ToolEvent"],
        config: dict,
    ) -> Optional[Verdict]:
        cfg = self._cfg(config)
        if not cfg.get("enabled", True):
            return None

        target = candidate or (events[-1] if events else None)
        if target is None:
            return None
        if str(getattr(target, "tool", "")).strip() != "Bash":
            return None

        cmd = str(getattr(target, "arg_preview", "") or "").strip()
        if not cmd:
            return None

        # Already invoking a venv python directly — leave it alone.
        if ".venv/bin/python" in cmd:
            return None

        if _first_token(cmd) != "python":
            return None

        venv = _repo_venv_python()
        if venv:
            target_hint = f"`{venv}` (repo venv) — preferred when present"
            fallback = "or `python3`"
        else:
            target_hint = "`python3`"
            fallback = "or `<repo>/.venv/bin/python` when a project venv exists"

        return Verdict(
            NUDGE,
            self.name,
            (
                f"`python` is often not aliased (macOS, many Linux distros). "
                f"Use {target_hint} {fallback}. The bare `python` command will "
                f"fail or run an unintended interpreter."
            ),
        )
