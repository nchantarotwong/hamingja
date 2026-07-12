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
_READ_CHUNK_BYTES = 64 * 1024


def _line_count_up_to(path_str: str, limit: int) -> Optional[int]:
    """Return min(line_count, limit), or None when the file cannot be inspected."""
    if not path_str:
        return None
    try:
        count = 0
        saw_any = False
        last = b""
        with Path(path_str).open("rb") as fh:
            while count < limit:
                chunk = fh.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                saw_any = True
                count += chunk.count(b"\n")
                last = chunk[-1:]
        if saw_any and last != b"\n":
            count += 1
        return min(count, limit)
    except Exception:
        return None


def _cfg_int(cfg: dict, key: str, default: int, floor: int = 1) -> int:
    try:
        return max(floor, int(cfg.get(key, default)))
    except Exception:
        return default


def _locator_hint(path: str) -> str:
    """Return a generic locator hint plus optional repo-local helper discovery."""
    hint = " Run `hamingja locate \"<what you are looking for>\"` before reading large files."
    try:
        cur = Path(path).resolve().parent
        for root in (cur, *cur.parents):
            if (root / "refs.sh").is_file():
                return hint + " Repo helper also available: `./refs.sh <symbol-or-pattern>`."
            scripts = root / "scripts"
            if (scripts / "refs.sh").is_file():
                return hint + " Repo helper also available: `scripts/refs.sh <symbol-or-pattern>`."
            if (root / ".git").exists():
                break
    except Exception:
        return hint
    return hint


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

        nudge_at = _cfg_int(cfg, "nudge_at", 2)
        block_at = max(nudge_at + 1, _cfg_int(cfg, "block_at", 3))
        first_read_block_at = _cfg_int(
            cfg,
            "block_first_read_at_lines",
            _FIRST_READ_BLOCK_LINE_THRESHOLD,
            floor=0,
        )
        count_limit = max(_LARGE_FILE_LINE_THRESHOLD, first_read_block_at)
        line_count = _line_count_up_to(path, count_limit)
        # Skip missing/unreadable/small files — fail open and keep overhead low.
        if line_count is None or line_count < _LARGE_FILE_LINE_THRESHOLD:
            return None

        name = Path(path).name
        hint = _locator_hint(path)

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
                    f"Unscoped Read of {name} blocked ({line_count}+ lines). "
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
