# AGENTS.md (Codex)

Codex reads `AGENTS.md` at the repository root. With the default
`hamingja init` workflow, `AGENTS.md` is a relative symlink to
`CLAUDE.md` (where the actual content lives), so Codex sees the same
profiles Claude Code does without any duplication.

If you want a Codex-specific addition that should NOT also appear in
Claude Code's context, break the symlink and use a real `AGENTS.md`
(e.g. `hamingja init --out AGENTS.md --no-link`), then append your
Codex-only notes under the header below.

---

## Codex notes

- Current Codex hooks cover shell and unified exec as `Bash`, `apply_patch`,
  MCP tools, and other local function tools. A running command's result hook
  may arrive through a later `write_stdin` poll. Hosted tools such as
  `WebSearch` are not covered, and older runtimes may expose fewer paths.
  The `error_streak` detector depends on emitted results — see the hamingja README.
- If you run `/hooks` and Codex asks you to trust the hamingja hooks, do so
  once. The `PreToolUse` hook may deny a tool call (in `enforce` mode) and
  records guardrail/audit state (a marker for the denied call, plus an entry
  in the verdict audit log behind `observe` mode). The `PostToolUse` hook
  records the outcome of each completed call. Neither hook reads or modifies
  your source files.
