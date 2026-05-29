"""Session-keyed rolling event store.

State lives in a temp dir, one JSONL file per session, so concurrent sessions
never poison each other's counters. We keep only the most recent `cap` events
to bound file growth.

Concurrency: a single session can have hooks fire close together (a PreToolUse
read interleaving a PostToolUse append/truncate, or batched tool calls). All
access is serialized with an advisory file lock (flock): writers take an
exclusive lock for the whole append+truncate, readers a shared lock. The
truncate is done in place under the exclusive lock, so a reader never observes
a half-rewritten file. flock is best-effort — if unavailable (e.g. Windows), we
degrade to lock-free access rather than failing.

Everything here is FAIL-OPEN: any error (missing dir, unreadable file, bad
line) degrades to a no-op or an empty result. The store can never be the
reason a tool call is blocked.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .events import ToolEvent

try:
    import fcntl  # POSIX only
    _HAVE_FCNTL = True
except Exception:  # pragma: no cover
    _HAVE_FCNTL = False


def _lock(fh, exclusive: bool) -> None:
    if not _HAVE_FCNTL:
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
    except Exception:
        pass


def _unlock(fh) -> None:
    if not _HAVE_FCNTL:
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass


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
    """Append an event, truncating to `cap`, under an exclusive lock. Never raises."""
    try:
        path = _session_file(event.session_id)
        # 'a+': created if absent; in append mode every write goes to EOF, which
        # — after an in-place truncate to 0 — means the rewrite lands at the start.
        with path.open("a+", encoding="utf-8") as fh:
            _lock(fh, exclusive=True)
            try:
                fh.write(event.to_json() + "\n")
                fh.flush()
                fh.seek(0)
                lines = fh.read().splitlines()
                if len(lines) > cap:
                    kept = "\n".join(lines[-cap:]) + "\n"
                    fh.seek(0)
                    fh.truncate()
                    fh.write(kept)
                    fh.flush()
            finally:
                _unlock(fh)
    except Exception:
        return


def read_recent(session_id: str, window: int) -> list[ToolEvent]:
    """Return up to `window` most-recent events (oldest first). [] on any error."""
    try:
        n = window if isinstance(window, int) and window > 0 else 1
        path = _session_file(session_id)
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as fh:
            _lock(fh, exclusive=False)
            try:
                data = fh.read()
            finally:
                _unlock(fh)
        events: list[ToolEvent] = []
        for line in data.splitlines()[-n:]:
            if not line.strip():
                continue
            try:
                events.append(ToolEvent.from_json(line))
            except Exception:
                continue  # a single corrupt line never sinks the read
        return events
    except Exception:
        return []
