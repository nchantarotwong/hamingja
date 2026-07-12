## Workflow rails (agent-rails)

Soft workflow rails for this repository — how to debug, when to escalate,
what to do when the user asks the agent to stop. Read by your coding agent
(Claude Code reads `CLAUDE.md` natively; Codex reads `AGENTS.md`).

These instructions are **advisory**. They are not a kill switch. The
deterministic safety layer — repetition / oscillation / error-streak
tripwires that can actually block a tool call — lives in the
[`agent-rails`](https://github.com/nchantarotwong/agent-rails) package itself and runs as a hook,
outside the agent's judgment. Nothing in this section blocks anything; the
agent is expected to follow these rails because they are how good
engineering work gets done, not because a hook will punish it.

This block is managed by `agent-rails init`. Anything you write *outside*
the surrounding `<!-- BEGIN/END agent-rails workflow profiles -->` markers
is preserved on re-run; anything *inside* is rewritten. Re-run
`agent-rails init` to update; pass `--force` to clobber the entire file.

---
