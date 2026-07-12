"""Workflow-wrapper detector — prefer project workflow wrappers over raw gh.

Raw GitHub CLI commands are easy to reach for, but hamingja ships wrappers
that preserve project workflow properties: concise CI status, failure
extraction, merge polling, and post-merge cleanup. This detector nudges on the
high-signal raw commands that those wrappers replace.
"""
from __future__ import annotations

import os
import shlex
from typing import Optional

from .base import BLOCK, Detector, Verdict


def _preview(candidate) -> str:
    return (getattr(candidate, "arg_preview", "") or "").strip()


def _is_shell(candidate) -> bool:
    if candidate is None:
        return False
    return (
        str(getattr(candidate, "arg_kind", "")).startswith("shell")
        or getattr(candidate, "tool", "") == "Bash"
    )


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


def _contains_git_cleanup(tokens: list[str]) -> bool:
    deletes_branch = _contains_sequence(tokens, ("git", "branch", "-d"))
    checkout_main = _contains_sequence(tokens, ("git", "checkout", "main"))
    ff_pull = _contains_sequence(tokens, ("git", "pull", "--ff-only"))
    return deletes_branch or (checkout_main and ff_pull)


def _has_inline_allow(tokens: list[str]) -> bool:
    return any(tok == "hamingja_allow_raw=1" for tok in tokens[:3])


def _wrapper_reason(wrapper: str, raw: str, cmd: str) -> str:
    return (
        f"Wrapper exists: use `{wrapper}` before raw `{raw}`. "
        "If the wrapper is unavailable or fails loudly, rerun the raw fallback "
        "with HAMINGJA_ALLOW_RAW=1 and say why. "
        f"Candidate command: {cmd}"
    )


class WorkflowWrapperDetector(Detector):
    name = "workflow_wrapper"

    def evaluate(self, events, candidate, config) -> Optional[Verdict]:
        if not _is_shell(candidate):
            return None
        if os.environ.get("HAMINGJA_ALLOW_RAW") == "1":
            return None

        cmd = _preview(candidate)
        tokens = _tokens(cmd)
        if not tokens:
            return None
        if _has_inline_allow(tokens):
            return None

        if _contains_sequence(tokens, ("gh", "pr", "create")):
            return Verdict(
                BLOCK,
                self.name,
                _wrapper_reason("hamingja pr-create --title <title> --body-file <path>", "gh pr create", cmd),
            )
        if _contains_sequence(tokens, ("gh", "pr", "checks")):
            return Verdict(
                BLOCK,
                self.name,
                _wrapper_reason("hamingja ci-status <pr>", "gh pr checks", cmd),
            )
        if _contains_sequence(tokens, ("gh", "pr", "merge")):
            return Verdict(
                BLOCK,
                self.name,
                _wrapper_reason("hamingja pr-merge <pr>", "gh pr merge", cmd),
            )
        if (
            _contains_sequence(tokens, ("gh", "run", "list"))
            or _contains_sequence(tokens, ("gh", "run", "view"))
            or _contains_sequence(tokens, ("gh", "run", "watch"))
        ):
            return Verdict(
                BLOCK,
                self.name,
                _wrapper_reason(
                    "hamingja ci-failures --pr <pr> or hamingja ci-failures --run <run-id>",
                    "gh run",
                    cmd,
                ),
            )
        if _contains_git_cleanup(tokens):
            return Verdict(
                BLOCK,
                self.name,
                _wrapper_reason("hamingja post-merge-cleanup [branch]", "manual git cleanup", cmd),
            )
        return None
