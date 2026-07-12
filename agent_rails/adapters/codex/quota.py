"""Codex quota probe — read the harness's own rate-limit signal.

Codex writes a session rollout at
``~/.codex/sessions/YYYY/MM/DD/rollout-<iso>-<session_id>.jsonl``. Every turn it
appends an ``event_msg`` of ``type: "token_count"`` whose payload carries the
*server-side* quota state:

    "rate_limits": {
      "primary":   {"used_percent": 16.0, "window_minutes": 300,   "resets_at": ...},
      "secondary": {"used_percent": 17.0, "window_minutes": 10080,  "resets_at": ...},
      "plan_type": "prolite"
    },
    "info": {"total_token_usage": {"total_tokens": ...}, "model_context_window": ...}

That is the real scarce resource on a CLI subscription — the 5-hour rolling
window (``primary``) and the weekly cap (``secondary``) as a used-percent with a
reset time — so we read it directly instead of modelling tokens or dollars.

Design constraints (both hard requirements):

* CHEAP — the rollout grows to tens of MB, but the newest ``token_count`` sits a
  few hundred bytes from EOF. We ``seek`` to a bounded window at the tail and
  scan *backwards* for the newest event, never parsing the whole file. The
  window doubles up to a cap only if the newest event isn't found; past the cap
  we give up (fail-open) rather than read the whole file.
* CONSISTENT — the rollout is append-only JSONL written by a live process, so a
  tail window can start mid-line and the final line can be a partial write. We
  therefore trust only lines that are fully delimited by newlines *inside* the
  window: the fragment before the first ``\\n`` (possibly truncated by the
  window) and the fragment after the last ``\\n`` (possibly an in-flight write)
  are both discarded. sqlite state DBs are avoided entirely — their WALs are not
  safe to read under concurrent writes.

FAIL-OPEN: every path returns ``None`` on any error, missing file, missing
field, or malformed JSON. A quota probe that raises would defeat the point.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .._jsonl_tail import iter_complete_lines_reversed

# Initial tail window and the cap it may grow to. 64 KiB comfortably covers a
# normal turn's worth of trailing data (observed: newest event ~1 KB from EOF);
# the cap bounds worst-case cost when a giant tool-output line precedes the
# newest token_count. Past the cap we fail open rather than scan the whole file.
_TAIL_INITIAL = 64 * 1024
_TAIL_CAP = 2 * 1024 * 1024
_READING_TTL_SECONDS = 300


@dataclass(frozen=True)
class QuotaReading:
    """A normalized, harness-neutral quota snapshot. Any field may be None."""

    window_used_pct: Optional[float] = None      # primary: 5-hour rolling window
    weekly_used_pct: Optional[float] = None      # secondary: weekly cap
    window_resets_at: Optional[int] = None        # epoch seconds
    weekly_resets_at: Optional[int] = None        # epoch seconds
    context_used_pct: Optional[float] = None      # total_tokens / model_context_window
    plan_type: Optional[str] = None
    source: str = "codex-rollout"


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _find_rollout(session_id: str, home: Path) -> Optional[Path]:
    """Locate the rollout file for a session id. Returns the newest match.

    Files are named ``rollout-<iso>-<session_id>.jsonl``. There are typically a
    few dozen; ``rglob`` on the suffix is cheap. If several match (shouldn't
    happen — session ids are unique), the most-recently-modified wins.
    """
    try:
        sid = str(session_id).strip()
        if not sid:
            return None
        sessions = home / "sessions"
        if not sessions.is_dir():
            return None
        matches = list(sessions.rglob(f"rollout-*-{sid}.jsonl"))
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        return max(matches, key=lambda p: p.stat().st_mtime)
    except Exception:
        return None


def _iter_complete_lines_reversed(path: Path):
    """Yield complete JSONL lines from the tail, newest first. Fail-open.

    Thin wrapper over the shared tail reader; reads module-level window
    constants at call time so tests can monkeypatch ``_TAIL_INITIAL`` /
    ``_TAIL_CAP``.
    """
    yield from iter_complete_lines_reversed(path, _TAIL_INITIAL, _TAIL_CAP)


def _extract(payload: dict) -> Optional[QuotaReading]:
    """Build a QuotaReading from a token_count event payload. None if unusable."""
    try:
        rl = payload.get("rate_limits")
        info = payload.get("info")
        info = info if isinstance(info, dict) else {}

        window_pct = weekly_pct = None
        window_reset = weekly_reset = None
        plan = None
        if isinstance(rl, dict):
            primary = rl.get("primary")
            secondary = rl.get("secondary")
            if isinstance(primary, dict):
                window_pct = _as_float(primary.get("used_percent"))
                window_reset = _as_int(primary.get("resets_at"))
            if isinstance(secondary, dict):
                weekly_pct = _as_float(secondary.get("used_percent"))
                weekly_reset = _as_int(secondary.get("resets_at"))
            p = rl.get("plan_type")
            if isinstance(p, str) and p.strip():
                plan = p.strip()

        # Context occupancy uses last_token_usage (the footprint of the most
        # recent request), NOT total_token_usage — the latter is CUMULATIVE
        # session usage (tens of millions of tokens on a long session) and would
        # peg every long session to 100%. last_token_usage.total_tokens is the
        # current turn's prompt+completion against the window ceiling.
        context_pct = None
        usage = info.get("last_token_usage")
        ctx_window = _as_int(info.get("model_context_window"))
        if isinstance(usage, dict) and ctx_window and ctx_window > 0:
            total = _as_int(usage.get("total_tokens"))
            if total is not None:
                context_pct = max(0.0, min(100.0, 100.0 * total / ctx_window))

        # If we recovered nothing usable, treat as no reading (fail-open).
        if (
            window_pct is None
            and weekly_pct is None
            and context_pct is None
        ):
            return None

        return QuotaReading(
            window_used_pct=window_pct,
            weekly_used_pct=weekly_pct,
            window_resets_at=window_reset,
            weekly_resets_at=weekly_reset,
            context_used_pct=context_pct,
            plan_type=plan,
        )
    except Exception:
        return None


def _as_float(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _as_int(v) -> Optional[int]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v)


def read_quota(session_id: str, home: Optional[Path] = None) -> Optional[QuotaReading]:
    """Return the newest Codex quota reading for a session, or None. Fail-open.

    Locates the session rollout, tails it for the newest ``token_count`` event,
    and normalizes its ``rate_limits`` + context usage into a QuotaReading.
    Returns None on any error, missing file/field, or if no usable event is
    within the tail cap.
    """
    try:
        base = home or _codex_home()
        path = _find_rollout(session_id, base)
        if path is None:
            return None
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return None
        if age < 0 or age > _READING_TTL_SECONDS:
            return None
        for raw in _iter_complete_lines_reversed(path):
            # Cheap pre-filter before json.loads: skip lines that can't be the
            # event we want. Avoids parsing large tool-output lines.
            if b"token_count" not in raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("type") != "event_msg":
                continue
            payload = obj.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            reading = _extract(payload)
            if reading is not None:
                return reading
        return None
    except Exception:
        return None
