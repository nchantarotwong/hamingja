"""Leverage-fallback detector — required tool failed, weaker fallback starts.

Some project tools are not just convenience wrappers. They preserve a property:
semantic navigation, generated/source freshness, schema validation, or a
stale-binary guard. If such a tool fails and the agent silently switches to a
weaker textual fallback, the session can look productive while bypassing the
property the tool exists to enforce.

This detector is configured with three explicit lists: required tool patterns,
weaker fallback patterns, and protected targets. Empty lists make it inert,
which keeps the public default safe until a project opts in from trusted config.
"""
from __future__ import annotations

from typing import Optional

from .base import BLOCK, Detector, Verdict
from ..core.events import ERROR


def _lower(e) -> str:
    return (getattr(e, "arg_preview", "") or "").lower()


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(n.lower() in text for n in needles)


def _cfg_list(cfg: dict, key: str) -> list[str]:
    value = cfg.get(key)
    if not isinstance(value, list):
        return []
    return [str(v).strip().lower() for v in value if str(v).strip()]


class LeverageFallbackDetector(Detector):
    name = "leverage_fallback"

    def evaluate(self, events, candidate, config) -> Optional[Verdict]:
        cfg = self._cfg(config)
        if not cfg.get("enabled", True):
            return None
        if candidate is None or candidate.tool != "Bash":
            return None

        required = _cfg_list(cfg, "required_patterns")
        fallback = _cfg_list(cfg, "fallback_patterns")
        targets = _cfg_list(cfg, "protected_targets")
        if not required or not fallback or not targets:
            return None

        preview = _lower(candidate)
        is_fallback = (
            _contains_any(preview, fallback)
            and _contains_any(preview, targets)
        )
        if not is_fallback:
            return None

        # Same-command bypass: `semantic-nav ... || grep ... protected-target`.
        if _contains_any(preview, required):
            return Verdict(
                BLOCK,
                self.name,
                "This command embeds a weak textual fallback next to a required "
                f"leverage tool: {candidate.arg_preview}. Split the steps. Run "
                "the leverage tool first; if it refuses, fix that tool or make "
                "the fallback explicit to the user before using broad search.",
            )

        lookback = int(cfg.get("lookback", 4))
        recent = events[-lookback:] if lookback > 0 else events
        for e in reversed(recent):
            if e.tool != "Bash" or e.status != ERROR:
                continue
            prior = _lower(e)
            if _contains_any(prior, required):
                return Verdict(
                    BLOCK,
                    self.name,
                    "A required leverage tool just failed, and the next command "
                    "is a weaker text-search fallback over its protected target. "
                    "Do not silently bypass semantic/freshness tooling. Fix the "
                    "tool path first, or stop and report the explicit fallback "
                    f"before running: {candidate.arg_preview}",
                )
        return None
