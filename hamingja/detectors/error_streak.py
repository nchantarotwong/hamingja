"""Consecutive-error detector — errors with no intervening success.

Secondary to repetition, and deliberately so: errors *alone* are normal —
legitimate debugging produces them constantly. The pathological signal is a
streak that doesn't resolve. The streak resets to zero on ANY success, so the
ordinary "something failed, the agent fixed it, it succeeded" path never trips
this — by the time a nudge would fire, a real correction has already cleared
the counter.
"""
from __future__ import annotations

from typing import Optional

from .base import BLOCK, NUDGE, Detector, Verdict
from ..core.events import ERROR


class ErrorStreakDetector(Detector):
    name = "error_streak"

    def evaluate(self, events, candidate, config) -> Optional[Verdict]:
        cfg = self._cfg(config)
        if not cfg.get("enabled", True):
            return None

        streak = 0
        for e in reversed(events):  # newest first
            if e.status == ERROR:
                streak += 1
            else:
                break

        block_at = int(cfg.get("block_at", 6))
        nudge_at = int(cfg.get("nudge_at", 3))

        if streak >= block_at:
            return Verdict(
                BLOCK,
                self.name,
                f"{streak} consecutive tool errors with no success in between. "
                f"Acting again before diagnosing tends to make this worse. "
                f"Stop. Write the symptom in one sentence and a single "
                f"hypothesis, then make one targeted measurement to test it.",
            )
        if streak >= nudge_at:
            return Verdict(
                NUDGE,
                self.name,
                f"{streak} consecutive tool errors. Before the next call, state "
                f"your current hypothesis explicitly — don't retry blind.",
            )
        return None
