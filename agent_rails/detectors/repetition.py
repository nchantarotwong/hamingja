"""Repetition detector — identical (tool, arg_hash) recurring.

The strongest flailing signal with the lowest false-positive rate. A model
making progress varies its commands; a model in a doom loop repeats the same
call expecting a different result. We count how many events in the window
match the candidate call's (tool, arg_hash). Because the count includes the
candidate itself, `block_at: 4` means "three identical calls already ran, deny
the fourth."

This never trips on legitimate correction, because correction *changes the
call*. It only fires on literal repetition.
"""
from __future__ import annotations

from typing import Optional

from .base import BLOCK, NUDGE, Detector, Verdict


class RepetitionDetector(Detector):
    name = "repetition"

    def evaluate(self, events, candidate, config) -> Optional[Verdict]:
        cfg = self._cfg(config)
        if not cfg.get("enabled", True):
            return None

        target = candidate or (events[-1] if events else None)
        if target is None:
            return None

        # Read-only / idempotent tools repeat legitimately (re-reading a file,
        # re-grepping, polling). Exempt them so a harmless repeated lookup never
        # trips a block. error_streak still covers a read that keeps erroring.
        exempt = cfg.get("exempt_tools")
        if isinstance(exempt, list) and target.tool in exempt:
            return None

        prior = sum(
            1 for e in events if e.tool == target.tool and e.arg_hash == target.arg_hash
        )
        # If we have a candidate, executing it makes (prior + 1) identical calls.
        # Without a candidate we're scoring history as-is.
        count = prior + 1 if candidate is not None else prior

        block_at = int(cfg.get("block_at", 4))
        nudge_at = int(cfg.get("nudge_at", 3))

        if count >= block_at:
            return Verdict(
                BLOCK,
                self.name,
                f"The same call ({target.tool}) is about to run for the "
                f"{count}th time with identical arguments. Repeating an "
                f"identical call is the signature of a stuck loop, not progress. "
                f"Stop. State the one symptom you're seeing and your single "
                f"current hypothesis, then make ONE different move.",
            )
        if count >= nudge_at:
            return Verdict(
                NUDGE,
                self.name,
                f"This is the {count}th identical {target.tool} call. If it "
                f"hasn't worked yet, the same call won't fix it — change "
                f"approach rather than retrying.",
            )
        return None
