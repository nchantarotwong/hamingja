"""Soft workflow profiles — reusable, agent-facing instruction fragments.

Profiles are pure markdown shipped in the wheel. They carry *workflow* defaults
(how to debug, when to escalate, what to do when the user asks "why is this
taking so long") — the kind of fuzzy guidance that has no business living in a
hard tripwire detector.

This module is read-only and offline: it is consulted by `hamingja init`
when it composes an `AGENTS.md` for a project. Nothing here runs on the
tool-call hot path, nothing here can block a call, nothing here participates
in the fail-open core. That separation is intentional — see README and
CLAUDE.md.

To add a profile: drop a new `<name>.md` file in this directory and append the
slug to `ALL_PROFILES` below (and to `DEFAULT_PROFILES` if it should be on by
default).
"""
from __future__ import annotations

from importlib.resources import files

# Order matters: this is the section order rendered into AGENTS.md.
DEFAULT_PROFILES: list[str] = [
    "base",
    "non_convergence",
    "debugging",
    "escalation",
    "review_passes",
]

# All shippable profiles. `compiler_language` is opt-in: it bakes in
# phase-based assumptions that are wrong for non-compiler work.
ALL_PROFILES: list[str] = DEFAULT_PROFILES + ["compiler_language"]


def normalize(name: str) -> str:
    """Canonicalize a user-supplied profile name (`compiler-language` -> `compiler_language`)."""
    return (name or "").strip().lower().replace("-", "_")


def list_profiles() -> list[str]:
    return list(ALL_PROFILES)


def read_profile(name: str) -> str:
    """Return the raw markdown body of a profile. Raises KeyError for unknown names."""
    slug = normalize(name)
    if slug not in ALL_PROFILES:
        raise KeyError(name)
    return files(__package__).joinpath(f"{slug}.md").read_text(encoding="utf-8")
