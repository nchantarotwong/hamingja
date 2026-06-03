# AGENTS.md (Codex)

Codex reads `AGENTS.md` at the repository root. With the default
`agent-rails init` workflow, `AGENTS.md` is a relative symlink to
`CLAUDE.md` (where the actual content lives), so Codex sees the same
profiles Claude Code does without any duplication.

If you want a Codex-specific addition that should NOT also appear in
Claude Code's context, break the symlink and use a real `AGENTS.md`
(e.g. `agent-rails init --out AGENTS.md --no-link`), then append your
Codex-only notes under the header below.

---

## Codex notes

- Codex hooks currently cover `Bash`, `apply_patch`, and MCP tools; newer
  shell-execution paths may bypass hook coverage. The `error_streak` detector
  is best-effort under Codex for that reason — see the agent-rails README.
- If you run `/hooks` and Codex asks you to trust the agent-rails hooks, do so
  once. The `PreToolUse` hook may deny a tool call (in `enforce` mode) and
  records guardrail/audit state (a marker for the denied call, plus an entry
  in the verdict audit log behind `observe` mode). The `PostToolUse` hook
  records the outcome of each completed call. Neither hook reads or modifies
  your source files.
