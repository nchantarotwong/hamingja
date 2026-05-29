"""agent-rails — harness-neutral guardrails for LLM coding agents.

The core question every detector answers is "is this agent flailing rather
than making progress?" — and the architecture keeps that question separate
from any particular agent harness:

    core/        normalized event schema + session state + the engine
    detectors/   pluggable signals (repetition, error-streak, ...) — the rules
    adapters/    thin per-harness glue (claude_code, generic, ...) — the I/O

Add a guardrail = a new file in detectors/. Add a harness = a new folder in
adapters/. Everything fails OPEN: any internal error defaults to allowing the
tool call, never blocking it. A guardrail that bricks your sessions is worse
than no guardrail.
"""

__version__ = "0.1.0"
