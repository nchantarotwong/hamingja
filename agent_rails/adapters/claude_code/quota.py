"""Claude Code quota probe — context-fill from the session transcript.

Unlike Codex, Claude Code does **not** persist a rate-limit used-percent to disk
(that lives on API response headers). The transcript
(``~/.claude/projects/<encoded-cwd>/<session_id>.jsonl``) does carry, per
assistant message, a ``usage`` block:

    "usage": {"input_tokens": ..., "cache_read_input_tokens": ...,
              "cache_creation_input_tokens": ..., "output_tokens": ...}

So the one real signal we can recover is **context occupancy** — how full the
model's context window is — which is itself a top CLI cost (a bloated context is
re-sent every turn). We therefore populate only ``context_used_pct``;
``window_used_pct`` / ``weekly_used_pct`` stay None, which means this reading can
*nudge* on context fill but never grants checkpoint relief (that requires the
rate-limit signal Codex has and Claude does not).

Two honest limitations, both fail-safe:

* The transcript does not record the context-window *size* (Codex hands us
  ``model_context_window``; Claude does not), so occupancy is estimated against
  a configurable denominator (``context_window_tokens``, default 200000). An
  over-large real window merely under-reports fill; it never over-blocks,
  because context fill only ever produces an advisory nudge.
* We read the newest completed assistant turn; the in-flight turn is not yet
  written. That is the right granularity for an advisory.

FAIL-OPEN: every path returns None on any error, missing file, or missing field.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .._jsonl_tail import iter_complete_lines_reversed

# Shared QuotaReading so the budget gate sees one harness-neutral shape.
from ..codex.quota import QuotaReading

_DEFAULT_CONTEXT_WINDOW = 200_000


def _claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def _find_transcript(session_id: str, home: Path, cwd: Optional[str]) -> Optional[Path]:
    """Locate the transcript for a session id. Returns the newest match.

    Transcripts are ``<session_id>.jsonl`` under a per-project directory. We glob
    by session id (unique) rather than reconstruct the cwd path-encoding, which
    is brittle. ``cwd`` is accepted for future narrowing but not required.
    """
    try:
        sid = str(session_id).strip()
        if not sid:
            return None
        projects = home / "projects"
        if not projects.is_dir():
            return None
        matches = list(projects.glob(f"*/{sid}.jsonl"))
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        return max(matches, key=lambda p: p.stat().st_mtime)
    except Exception:
        return None


def _as_int(v) -> Optional[int]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v)


def _context_pct(usage: dict, window: int) -> Optional[float]:
    """Estimate context occupancy from an assistant usage block. None if unusable.

    Occupancy is the input side of the request — the whole prompt the model saw:
    fresh input + cache-read + cache-creation tokens. output_tokens is the new
    completion (folded into the *next* turn's input, negligible here).
    """
    if not isinstance(usage, dict) or not window or window <= 0:
        return None
    total = 0
    seen = False
    for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        v = _as_int(usage.get(key))
        if v is not None:
            total += max(0, v)
            seen = True
    if not seen:
        return None
    return max(0.0, min(100.0, 100.0 * total / window))


def read_quota(
    session_id: str,
    cwd: Optional[str] = None,
    context_window_tokens: int = _DEFAULT_CONTEXT_WINDOW,
    home: Optional[Path] = None,
) -> Optional[QuotaReading]:
    """Return a context-fill QuotaReading for a Claude session, or None. Fail-open.

    Tails the session transcript for the newest assistant message carrying a
    ``usage`` block and reports ``context_used_pct``. ``window_used_pct`` /
    ``weekly_used_pct`` are always None (Claude does not persist them).
    """
    try:
        base = home or _claude_home()
        path = _find_transcript(session_id, base, cwd)
        if path is None:
            return None
        try:
            window = int(context_window_tokens)
        except (TypeError, ValueError):
            window = _DEFAULT_CONTEXT_WINDOW
        if window <= 0:
            window = _DEFAULT_CONTEXT_WINDOW

        for raw in iter_complete_lines_reversed(path):
            # Cheap pre-filter before json.loads: only assistant usage lines
            # matter. Skips large tool-result records.
            if b"usage" not in raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            message = obj.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            pct = _context_pct(usage, window) if isinstance(usage, dict) else None
            if pct is not None:
                return QuotaReading(
                    window_used_pct=None,
                    weekly_used_pct=None,
                    context_used_pct=pct,
                    plan_type=None,
                    source="claude-transcript",
                )
        return None
    except Exception:
        return None
