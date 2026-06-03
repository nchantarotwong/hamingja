"""Installable instruction templates (AGENTS.md / CLAUDE.md / Codex variant).

Templates are pure markdown shipped in the wheel. `agent-rails init` uses the
root `AGENTS.md` template as the header for the file it composes; the other
templates (`CLAUDE.md`, `codex/AGENTS.md`) are here for users who want to copy
them in manually.

Like the profiles, nothing in this package runs on the tool-call path.
"""
from __future__ import annotations

from importlib.resources import files

ROOT_TEMPLATE = "AGENTS.md"


def read_template(name: str) -> str:
    """Read a template by relative name, e.g. `AGENTS.md` or `codex/AGENTS.md`."""
    return files(__package__).joinpath(name).read_text(encoding="utf-8")
