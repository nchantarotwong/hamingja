# AGENTS.md (Codex)

Codex reads `AGENTS.md` at the repository root. This variant is the same
content as the default `AGENTS.md` template, with a short Codex-specific note
appended.

Use the default `agent-rails init` output as your `AGENTS.md`. If you want a
Codex-specific addition, append it under the header below.

---

## Codex notes

- Codex hooks currently cover `Bash`, `apply_patch`, and MCP tools; newer
  shell-execution paths may bypass hook coverage. The `error_streak` detector
  is best-effort under Codex for that reason — see the agent-rails README.
- If you run `/hooks` and Codex asks you to trust the agent-rails hooks, do so
  once; the hooks are read-only on `PreToolUse` and only record state on
  `PostToolUse`.
