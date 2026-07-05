"""Shared fail-open tail reader for append-only JSONL session logs.

Both harness quota probes (Codex rollout, Claude transcript) need to read the
*newest* record from a large, live, append-only JSONL file without parsing the
whole thing. The consistency rules are identical and safety-critical, so they
live here once rather than drifting between two copies:

* CHEAP — seek to a bounded window at the tail; never read the body. Grow the
  window (doubling, up to a cap) only if the caller keeps consuming without
  finding its record; past the cap, stop (the caller then fails open).
* CONSISTENT — the file is written by a live process, so a window can start
  mid-line and the final line can be a partial write. Only lines fully
  delimited by newlines *inside* the window are trusted: the fragment before
  the first ``\\n`` (possibly truncated by the window) and the fragment after
  the last ``\\n`` (possibly an in-flight write) are both discarded.

FAIL-OPEN: any error yields no lines (an empty generator), never raises.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

# 64 KiB comfortably covers a normal turn's trailing data; the cap bounds
# worst-case cost when a giant line precedes the record of interest.
DEFAULT_INITIAL = 64 * 1024
DEFAULT_CAP = 2 * 1024 * 1024


def iter_complete_lines_reversed(
    path: Path,
    initial: int = DEFAULT_INITIAL,
    cap: int = DEFAULT_CAP,
) -> Iterator[bytes]:
    """Yield complete JSONL lines from the tail of ``path``, newest first.

    Grows the read window up to ``cap`` if the caller exhausts it. Callers
    should stop at the first line they want; the overlap re-yielded on each
    growth step is bounded by ``cap``.
    """
    try:
        size = path.stat().st_size
    except Exception:
        return
    if size <= 0:
        return

    window = max(1, int(initial))
    cap = max(window, int(cap))
    while True:
        read_from = max(0, size - window)
        try:
            with path.open("rb") as fh:
                fh.seek(read_from)
                blob = fh.read(size - read_from)
        except Exception:
            return

        parts = blob.split(b"\n")
        # Final element follows the last newline: an in-flight partial write (or
        # empty if the file ends in \n). Never trust it.
        parts = parts[:-1]
        # If we did not reach the start of the file, the first element may be a
        # line truncated by the window boundary. Drop it; a wider window
        # recovers it if needed.
        reached_start = read_from == 0
        if not reached_start and parts:
            parts = parts[1:]

        for raw in reversed(parts):
            if raw.strip():
                yield raw

        if reached_start or window >= cap:
            return
        window = min(window * 2, cap)
