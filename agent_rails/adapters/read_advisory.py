"""Shared large-read advisory helpers for hook adapters.

These helpers are advisory only.  Any inspection failure returns no advisory so
the adapter fails open and lets the tool call proceed.
"""
from __future__ import annotations

from pathlib import Path

LARGE_FILE_LINE_THRESHOLD = 200


def large_read_line_count(tool: str, args: object) -> int:
    """Return line count for an unscoped large Read, else 0."""
    if not isinstance(args, dict):
        return 0
    if str(tool).strip().lower() != "read":
        return 0
    has_offset = args.get("offset") not in (None, "")
    has_limit = args.get("limit") not in (None, "")
    if has_offset or has_limit:
        return 0
    path_str = str(args.get("file_path") or args.get("path") or "").strip()
    if not path_str:
        return 0
    try:
        line_count = Path(path_str).read_bytes().count(b"\n")
    except OSError:
        return 0
    return line_count if line_count >= LARGE_FILE_LINE_THRESHOLD else 0


def large_read_advisory(tool: str, args: object) -> str | None:
    """Return an advisory string for unscoped reads of large files."""
    line_count = large_read_line_count(tool, args)
    if not line_count:
        return None
    path_str = str(args.get("file_path") or args.get("path") or "")  # type: ignore[union-attr]
    name = Path(path_str).name if path_str else "file"
    return (
        f"[agent-rails] {name} has ~{line_count} lines. "
        f"Prefer: `agent-rails locate \"<what you need>\"`, then Read only "
        f"the suggested line range with offset+limit. If you need a map first, "
        f"run `agent-rails code-atlas`. Unscoped reads of large files are the "
        f"primary source of excess token usage in a session."
    )
