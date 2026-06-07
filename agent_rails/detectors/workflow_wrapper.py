"""Workflow-wrapper detector — prefer project workflow wrappers over raw gh.

Raw GitHub CLI commands are easy to reach for, but agent-rails ships wrappers
that preserve project workflow properties: concise CI status, failure
extraction, merge polling, and post-merge cleanup. This detector nudges on the
high-signal raw commands that those wrappers replace.
"""
from __future__ import annotations

import shlex
from typing import Optional

from .base import BLOCK, Detector, Verdict


def _preview(candidate) -> str:
    return (getattr(candidate, "arg_preview", "") or "").strip()


def _is_bash(candidate) -> bool:
    return bool(candidate is not None and getattr(candidate, "tool", "") == "Bash")


def _tokens(cmd: str) -> list[str]:
    try:
        return [tok.lower() for tok in shlex.split(cmd)]
    except ValueError:
        return []


def _contains_sequence(tokens: list[str], sequence: tuple[str, ...]) -> bool:
    if not sequence or len(tokens) < len(sequence):
        return False
    last = len(tokens) - len(sequence) + 1
    for i in range(last):
        if tuple(tokens[i:i + len(sequence)]) == sequence:
            return True
    return False


class WorkflowWrapperDetector(Detector):
    name = "workflow_wrapper"

    def evaluate(self, events, candidate, config) -> Optional[Verdict]:
        if not _is_bash(candidate):
            return None

        cmd = _preview(candidate)
        tokens = _tokens(cmd)
        if not tokens:
            return None

        if _contains_sequence(tokens, ("gh", "pr", "checks")):
            return Verdict(
                BLOCK,
                self.name,
                "Use `agent-rails ci-status <pr>` before raw `gh pr checks`; "
                "the wrapper tracks the supported gh JSON schema and reports "
                f"a concise status. Candidate command: {cmd}",
            )
        if _contains_sequence(tokens, ("gh", "pr", "merge")):
            return Verdict(
                BLOCK,
                self.name,
                "Use `agent-rails pr-merge <pr>` before raw `gh pr merge`; "
                "the wrapper polls merge state and runs post-merge cleanup. "
                f"Candidate command: {cmd}",
            )
        if (
            _contains_sequence(tokens, ("gh", "run", "list"))
            or _contains_sequence(tokens, ("gh", "run", "view"))
        ):
            return Verdict(
                BLOCK,
                self.name,
                "Use `agent-rails ci-failures --pr <pr>` or "
                "`agent-rails ci-failures --run-id <id>` before raw `gh run` "
                f"failure spelunking. Candidate command: {cmd}",
            )
        return None
