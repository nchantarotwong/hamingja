"""ReadDisciplineDetector — nudge/block on unscoped reads of large files.

An *unscoped* Read has no offset and no limit: the model will receive the entire
file regardless of size.  A single unscoped read of a large file gets an
advisory from the tripwire hook (cheap, no history needed).  This detector
handles two higher-signal cases:
  * repeated unscoped reads of the same large file;
  * the first unscoped read of a genuinely huge file.

Design notes:
  * Only fires on Read events where read_scoped=False and read_path is set.
  * Counts unscoped reads PER PATH in the event window.  A subsequent scoped
    (offset/limit) read of the same file does NOT count and is not penalized.
  * Exempt when the file is small; the tripwire advisory already gated on
    _LARGE_FILE_LINE_THRESHOLD, but we also skip if we can stat it here.
  * Fails open: any exception returns None.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .base import BLOCK, NUDGE, Detector, Verdict

if TYPE_CHECKING:
    from ..core.events import ToolEvent

_LARGE_FILE_LINE_THRESHOLD = 200
_FIRST_READ_BLOCK_LINE_THRESHOLD = 1000


def _line_count(path_str: str) -> Optional[int]:
    """Return a file's line count, or None when it cannot be inspected."""
    if not path_str:
        return None
    try:
        return len(Path(path_str).read_bytes().splitlines())
    except OSError:
        return None


def _cfg_int(cfg: dict, key: str, default: int, floor: int = 1) -> int:
    try:
        return max(floor, int(cfg.get(key, default)))
    except Exception:
        return default


def _refs_hint(path: str) -> str:
    """Return an optional repo-local reference lookup hint."""
    try:
        cur = Path(path).resolve().parent
        for root in (cur, *cur.parents):
            if (root / "refs.sh").is_file():
                return " If available, use `./refs.sh <symbol-or-pattern>` to locate references first."
            scripts = root / "scripts"
            if (scripts / "refs.sh").is_file():
                return " If available, use `scripts/refs.sh <symbol-or-pattern>` to locate references first."
            if (root / ".git").exists():
                break
    except OSError:
        return ""
    return ""


class ReadDisciplineDetector(Detector):
    name = "read_discipline"

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

        # Only fire on Read events that are already flagged unscoped.
        if getattr(target, "read_scoped", True):
            return None
        path = getattr(target, "read_path", "")
        if not path:
            return None

        line_count = _line_count(path)
        # Skip missing/unreadable/small files — fail open and keep overhead low.
        if line_count is None or line_count < _LARGE_FILE_LINE_THRESHOLD:
            return None

        nudge_at = _cfg_int(cfg, "nudge_at", 2)
        block_at = max(nudge_at + 1, _cfg_int(cfg, "block_at", 3))
        first_read_block_at = _cfg_int(
            cfg,
            "block_first_read_at_lines",
            _FIRST_READ_BLOCK_LINE_THRESHOLD,
            floor=0,
        )
        name = Path(path).name
        hint = _refs_hint(path)

        # Count prior unscoped reads of the same path in the event window.
        prior_unscoped = sum(
            1
            for e in events
            if (
                not getattr(e, "read_scoped", True)
                and getattr(e, "read_path", "") == path
                and e is not target
            )
        )
        # +1 to include the current candidate itself.
        total = prior_unscoped + 1

        if first_read_block_at > 0 and total == 1 and line_count >= first_read_block_at:
            return Verdict(
                BLOCK,
                self.name,
                (
                    f"Unscoped Read of {name} blocked ({line_count} lines). "
                    f"Use grep -n to locate the target section, then Read with "
                    f"offset+limit.{hint}"
                ),
            )

        if total < nudge_at:
            return None

        if total >= block_at:
            return Verdict(
                BLOCK,
                self.name,
                (
                    f"Unscoped Read of {name} blocked (read {total}x without "
                    f"offset/limit). Use grep -n to locate the target section, "
                    f"then Read with offset+limit.{hint}"
                ),
            )

        return Verdict(
            NUDGE,
            self.name,
            (
                f"Unscoped Read of {name} again ({total}x). Next unscoped read "
                f"of this file will be blocked. Use grep -n to find the section, "
                f"then Read with offset+limit.{hint}"
            ),
        )
