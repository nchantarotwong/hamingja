"""hamingja — fail-open partner rails for Codex and Claude Code.

The core question every detector answers is "is this agent flailing rather
than making progress?" — and the architecture keeps that question separate
from any particular agent harness:

    core/        normalized event schema + session state + the engine
    detectors/   pluggable signals (repetition, error-streak, ...) — the rules
    adapters/    thin per-harness glue (claude_code, generic, ...) — the I/O

Add a guardrail = a new file in detectors/. Add a harness = a new folder in
adapters/. Guardrail evaluation fails OPEN: internal uncertainty defaults to
allowing the tool call, never inventing a denial.
"""

__version__ = "0.1.0"
