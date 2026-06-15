"""ReadDisciplineDetector — nudge/block on repeated unscoped reads of large files.

An *unscoped* Read has no offset and no limit: the model will receive the entire
file regardless of size.  A single unscoped read of a large file gets an
advisory from the tripwire hook (cheap, no history needed).  This detector
handles the repeat-offense case: if the model reads the same large file
unscoped a second time in a session it is nudged; a third unscoped read of the
same file is blocked.

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


def _is_large(path_str: str) -> bool:
    """True when the file exists and exceeds the line threshold."""
    if not path_str:
        return False
    try:
        return Path(path_str).read_bytes().count(b"\n") >= _LARGE_FILE_LINE_THRESHOLD
    except OSError:
        return False


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

        # Skip small files — the overhead is already low.
        if not _is_large(path):
            return None

        nudge_at = max(1, int(cfg.get("nudge_at", 2)))
        block_at = max(nudge_at + 1, int(cfg.get("block_at", 3)))
        name = Path(path).name

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

        if total < nudge_at:
            return None

        if total >= block_at:
            return Verdict(
                BLOCK,
                self.name,
                (
                    f"Unscoped Read of {name} blocked (read {total}x without "
                    f"offset/limit). Use grep -n to locate the target section, "
                    f"then Read with offset+limit."
                ),
            )

        return Verdict(
            NUDGE,
            self.name,
            (
                f"Unscoped Read of {name} again ({total}x). Next unscoped read "
                f"of this file will be blocked. Use grep -n to find the section, "
                f"then Read with offset+limit."
            ),
        )
