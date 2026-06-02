"""Oscillation detector — the agent cycling between a few states.

`repetition` only fires on the SAME call repeating. But a poisoned context
often flips between two (or three) calls instead: apply fix A, see it fail,
apply fix B, see THAT fail, revert to A, and round again. Every individual call
"changes," so repetition stays quiet — yet the agent is just as stuck, walking
a closed loop.

This detector looks for a short repeating CYCLE (period 2, 3, or 4) in the
trailing call sequence. It requires the cycle to contain at least two DISTINCT
calls, so pure repetition (A-A-A-A) is left to the repetition detector and never
double-counted here. Like repetition, the candidate call is treated as the next
element, so a block fires *before* the call that would close another lap runs.

Read-only / idempotent tools are exempted (a window made up entirely of exempt
tools — e.g. alternately re-reading two files to compare them — is not
flailing), reusing whatever exempt_tools any detector declares.
"""
from __future__ import annotations

from typing import Optional

from .base import BLOCK, NUDGE, Detector, Verdict


def _key(e):
    return (e.tool, e.arg_hash)


def _trailing_period_run(seq: list, p: int) -> int:
    """How many trailing elements form a consistent period-`p` pattern.

    Walk backwards counting positions where seq[i] == seq[i-p]; the run is those
    matches plus the `p` baseline elements of the final cycle. 0 if none match.
    """
    n = len(seq)
    if n < 2 * p:
        return 0
    matched = 0
    i = n - 1
    while i - p >= 0 and seq[i] == seq[i - p]:
        matched += 1
        i -= 1
    return matched + p if matched else 0


def _exempt_tools(config: dict) -> set:
    """Union of exempt_tools across all detector configs (defined on repetition)."""
    out: set = set()
    for dc in (config.get("detectors") or {}).values():
        if isinstance(dc, dict) and isinstance(dc.get("exempt_tools"), list):
            out.update(dc["exempt_tools"])
    return out


class OscillationDetector(Detector):
    name = "oscillation"

    def evaluate(self, events, candidate, config) -> Optional[Verdict]:
        cfg = self._cfg(config)
        if not cfg.get("enabled", True):
            return None

        seq = [_key(e) for e in events]
        if candidate is not None:
            seq.append(_key(candidate))
        if len(seq) < 4:
            return None

        block_at = int(cfg.get("block_at", 6))
        nudge_at = int(cfg.get("nudge_at", 4))
        exempt = _exempt_tools(config)

        best_run = 0
        best_period = 0
        for p in (2, 3, 4):
            run = _trailing_period_run(seq, p)
            if run < 2 * p:
                continue  # need at least two full cycles to call it a loop
            window = seq[-run:]
            if len({k for k in window}) < 2:
                continue  # pure repetition — that's the repetition detector's job
            if all(tool in exempt for tool, _ in window):
                continue  # a loop made only of read-only lookups is not flailing
            if run > best_run:
                best_run = run
                best_period = p

        if not best_run:
            return None

        distinct = len({k for k in seq[-best_run:]})
        if best_run >= block_at:
            return Verdict(
                BLOCK,
                self.name,
                f"The last {best_run} tool calls are cycling between "
                f"{distinct} repeating states (period {best_period}). Oscillating "
                f"between the same handful of moves is a stuck loop wearing a "
                f"disguise — each call looks new but the set isn't growing. Stop. "
                f"Name the one thing you keep undoing and why, then make a move "
                f"that leaves that loop entirely.",
            )
        if best_run >= nudge_at:
            return Verdict(
                NUDGE,
                self.name,
                f"You're alternating between {distinct} repeating calls "
                f"(period {best_period}). If neither has worked, a third lap "
                f"won't either — break the cycle with a different approach.",
            )
        return None
