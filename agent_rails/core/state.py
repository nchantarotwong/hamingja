"""Session-keyed rolling event store.

State lives in a temp dir, one JSONL file per session, so concurrent sessions
never poison each other's counters. We keep only the most recent `cap` events
to bound file growth.

Everything here is FAIL-OPEN: any error (missing dir, unreadable file, bad
line) degrades to a no-op or an empty result. The store can never be the
reason a tool call is blocked.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .events import ToolEvent


def _state_dir() -> Path:
    base = os.environ.get("AGENT_RAILS_STATE_DIR") or os.path.join(
        tempfile.gettempdir(), "agent-rails"
    )
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _session_file(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return _state_dir() / f"{safe or 'default'}.jsonl"


def append_event(event: ToolEvent, cap: int = 200) -> None:
    """Append an event to its session log. Never raises."""
    try:
        path = _session_file(event.session_id)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(event.to_json() + "\n")
        _truncate(path, cap)
    except Exception:
        return


def _truncate(path: Path, cap: int) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > cap:
            path.write_text("\n".join(lines[-cap:]) + "\n", encoding="utf-8")
    except Exception:
        return


def read_recent(session_id: str, window: int) -> list[ToolEvent]:
    """Return up to `window` most-recent events (oldest first). [] on any error."""
    try:
        path = _session_file(session_id)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        events: list[ToolEvent] = []
        for line in lines[-window:]:
            if not line.strip():
                continue
            try:
                events.append(ToolEvent.from_json(line))
            except Exception:
                continue  # a single corrupt line never sinks the read
        return events
    except Exception:
        return []
