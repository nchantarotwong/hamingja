"""Repetition detector — identical (tool, arg_hash) recurring.

The strongest flailing signal with the lowest false-positive rate is not just
"same wrapper tool again"; it is the same complete tool identity recurring
without new evidence. Enforcement therefore requires a complete normalized
argument payload and strong repeat evidence (same failure or same output). This
keeps observe-mode nudges useful while making enforce-mode safe enough for real
agent work.
"""
from __future__ import annotations

from typing import Optional

from .base import BLOCK, NUDGE, Detector, Verdict
from ..core.events import BLOCKED, ERROR


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _output_hashes(events) -> list[str]:
    return [e.output_hash for e in events if getattr(e, "output_hash", "")]


def _failure_or_blocked_evidence(events, min_errors: int) -> bool:
    trailing_run = []
    for event in reversed(events):
        if event.status not in {ERROR, BLOCKED}:
            break
        trailing_run.append(event)
    blocked_count = len([e for e in trailing_run if e.status == BLOCKED])
    error_count = len([e for e in trailing_run if e.status == ERROR])
    return (
        bool(trailing_run)
        and (error_count >= min_errors or blocked_count > 0)
    )


def _is_read_only_shell(event) -> bool:
    return getattr(event, "arg_kind", "") == "shell:read-only"


def _is_low_noise_shell(event) -> bool:
    return getattr(event, "arg_kind", "") in {
        "shell:read-only", "shell:test", "shell:build", "shell:diagnostic-script"
    }


class RepetitionDetector(Detector):
    name = "repetition"

    def evaluate(self, events, candidate, config) -> Optional[Verdict]:
        cfg = self._cfg(config)
        if not cfg.get("enabled", True):
            return None

        target = candidate or (events[-1] if events else None)
        if target is None:
            return None
        if getattr(target, "args_complete", True) is False:
            return None

        # Read-only / idempotent tools repeat legitimately (re-reading a file,
        # re-grepping, polling). Exempt them so a harmless repeated lookup never
        # trips a block. error_streak still covers a read that keeps erroring.
        exempt = cfg.get("exempt_tools")
        if isinstance(exempt, list) and target.tool in exempt:
            return None

        matches = [
            e for e in events
            if e.tool == target.tool and e.arg_hash == target.arg_hash
            and getattr(e, "args_complete", True) is not False
        ]
        prior = len(matches)
        # If we have a candidate, executing it makes (prior + 1) identical calls.
        # Without a candidate we're scoring history as-is.
        count = prior + 1 if candidate is not None else prior

        block_at = int(cfg.get("block_at", 4))
        nudge_at = int(cfg.get("nudge_at", 3))

        if count < nudge_at:
            return None

        preview = getattr(target, "arg_preview", "") or target.tool
        nth = _ordinal(count)
        output_hashes = _output_hashes(matches)
        min_evidence = max(2, nudge_at - 1)
        repeated_output = len(output_hashes) >= min_evidence and len(set(output_hashes)) == 1
        repeated_failures = _failure_or_blocked_evidence(matches, min_evidence)
        strong_evidence = repeated_output or repeated_failures

        if not strong_evidence and _is_read_only_shell(target):
            return None

        if not strong_evidence and _is_low_noise_shell(target) and count < block_at:
            return None

        if count >= block_at and repeated_output and _is_read_only_shell(target):
            return Verdict(
                NUDGE,
                self.name,
                f"This is the {nth} repeated read-only shell command: {preview}, "
                f"with identical arguments and the same output. If you are "
                f"still learning something new, continue; if the output is "
                f"unchanged, switch to a different diagnostic.",
            )

        if count >= block_at and strong_evidence:
            reason = "the same output" if repeated_output else "the same failure"
            return Verdict(
                BLOCK,
                self.name,
                f"The same call ({target.tool}: {preview}) is about to run for the "
                f"{nth} time with identical arguments and {reason}. Repeating "
                f"that call is the signature of a stuck loop, not progress. "
                f"Stop. State the one symptom you're seeing and your single "
                f"current hypothesis, then make ONE different move.",
            )

        if count >= block_at:
            return Verdict(
                NUDGE,
                self.name,
                f"This is the {nth} identical {target.tool} call: {preview}. "
                f"I did not see repeated-output or repeated-failure evidence "
                f"strong enough to block it, but this is close to a loop. "
                f"Change approach if this does not produce new information.",
            )

        return Verdict(
            NUDGE,
            self.name,
            f"This is the {nth} identical {target.tool} call: {preview}. If it "
            f"hasn't worked yet, the same call won't fix it — change approach "
            f"rather than retrying.",
        )
