# agent-rails

Harness-neutral guardrails for LLM coding agents.

When an agent's context gets *poisoned* — tool calls start chain-erroring, it
conditions on its own failures, makes worse decisions, and loops — telling it
to stop usually doesn't work, because the instruction is just more tokens in a
context the failure pattern already dominates. The thing that's broken is the
agent's judgment, so the circuit breaker has to live **outside** its judgment.

agent-rails is that circuit breaker: a deterministic monitor that watches the
tool-call stream, detects the mechanical signature of flailing (not "errors" —
**errors with no novelty**), and interrupts the loop before it burns tokens.

## The one rule: fail open

A guardrail that fails *closed* — blocking tool calls because of its own bug —
is worse than no guardrail, especially when installed globally across every
project. So **every layer here fails open**: any error (unparseable input,
unreadable state, a throwing detector, bad config) defaults to *allowing* the
call. The only thing that ever blocks a call is an explicit, tested tripwire.

## What it detects

Ranked by signal quality (lowest false-positive rate first):

| Detector | Fires on | Why it's safe |
|---|---|---|
| `repetition` | the **same** `(tool, args)` call repeating | progress *varies* calls; only literal repetition trips it — legitimate correction changes the call |
| `error_streak` | consecutive errors with no success between | resets to zero on **any** success, so "failed → fixed → succeeded" never trips it |

Add a guardrail = a new file in `detectors/` implementing the `Detector`
interface, registered in `core/engine.py`.

## Graduated response, not a kill switch

* **nudge** — inject an advisory into the agent's context ("3rd identical call;
  change approach"). Non-blocking. This is just forcing the
  state-then-hypothesis discipline at the right moment.
* **block** — deny the call until the agent writes a diagnosis and changes
  course. A hard block is the only thing that reliably interrupts the loop,
  because injected text can be ignored by a poisoned context but a denied call
  cannot.

## Modes (safe rollout)

* `observe` *(default)* — never blocks; emits a nudge carrying `would_block`, so
  you can tune thresholds against your real workflow before enforcing.
* `enforce` — blocks for real.
* `off` — disabled.

**Per-repo opt-out:** drop a `.agent-rails-off` file at the repo root and the
guard stands down there — recording goes inert too — for repos that
legitimately flail (long migrations, known-noisy tasks). It's honored even when
the agent runs in a subdirectory.

### Config & trust model

Configuration resolves in this order, and the trust boundary matters:

1. built-in defaults
2. packaged `agent_rails/config.default.json` — **trusted** (ships with the install)
3. per-project `.agent-rails.json`, searched from the agent's cwd up to the
   repo root — **untrusted**: it may only *relax* the guard (raise thresholds,
   disable detectors, lower the window, downgrade mode toward `off`). It can
   **never** escalate to `enforce` or lower a threshold, so a hostile or
   careless repo cannot brick the agent by forcing its first tool call to be
   denied.
4. `.agent-rails-off` marker (same upward search) → `off`
5. `AGENT_RAILS_MODE` env var — **trusted** (your shell); may set any mode.

All values are sanitized: modes are canonicalized, and `window`/`block_at`/
`nudge_at` are coerced to ints with safe floors, so a typo or out-of-range
value can neither crash a detector nor cause a spurious block.

## Architecture

```
agent_rails/
  core/        events.py   normalized ToolEvent (the harness-neutral schema)
               state.py    session-keyed rolling log (locked, fail-open)
               engine.py   run enabled detectors -> aggregate -> verdict
               api.py      check()/record() — the one entry point adapters call
  detectors/   base.py     Detector interface + Verdict
               repetition.py, error_streak.py
               config.default.json   packaged trusted defaults (ships in the wheel)
  adapters/    claude_code/  PreToolUse tripwire + PostToolUse recorder + install.sh
               codex/        PreToolUse tripwire + PostToolUse recorder + install.sh
               generic/      observe()/check() for any custom agent loop
tests/         synthetic-sequence unit tests
```

The detector core is pure and harness-agnostic. Each adapter only translates a
harness's native payload into a `ToolEvent` and a verdict back into that
harness's response. Two ingestion modes are supported by design:

1. **Inline hook** (synchronous, *can block*) — e.g. the Claude Code adapter.
2. **Transcript-tail / supervisor** (asynchronous, *observe + kill + notify*) —
   the universal fallback for harnesses without pre-call hooks, and the basis
   for an at-scale fleet watchdog. *(planned)*

## Install (Claude Code)

```bash
bash agent_rails/adapters/claude_code/install.sh
```

Merges three hooks into `~/.claude/settings.json` — a `PreToolUse` tripwire and
a recorder on both `PostToolUse` (success) and `PostToolUseFailure` (failure, so
error detection is by event, not by parsing an undocumented result shape). The
merge preserves your other hooks, is idempotent, self-heals a moved repo path,
and backs up only when it actually changes something. Default mode is `observe`,
so nothing is blocked until you flip to `enforce`.

## Install (Codex)

```bash
bash agent_rails/adapters/codex/install.sh
```

Merges two hooks into `~/.codex/hooks.json` — a `PreToolUse` tripwire and a
`PostToolUse` recorder. The merge preserves your other hooks, is idempotent,
self-heals a moved repo path, and backs up only when it actually changes
something. Codex may ask you to review/trust the new hooks with `/hooks`.
Default mode is `observe`, so nothing is blocked until you flip to `enforce`.

Codex hook coverage follows Codex's hook support: `PreToolUse` / `PostToolUse`
currently cover Bash, `apply_patch`, and MCP tools, but not every possible
tool path. In particular, newer shell execution paths may bypass tool hooks;
when Codex does not emit `PostToolUse`, agent-rails cannot observe that result,
so the `error_streak` detector is best-effort for Codex. `repetition` still
works for any `PreToolUse`-covered call.

## Use in your own agent loop

```python
from agent_rails.adapters.generic import observe, check

verdict = check(session_id, tool_name, tool_args)   # before the call
if verdict.action == "block":
    ...                                              # re-plan; verdict.reason says why
observe(session_id, tool_name, tool_args, ok=succeeded)  # after the call
```

## Tests

```bash
python -m pytest          # if pytest is installed
python tests/test_detectors.py   # or run any test file directly
```

Fixtures are **synthetic only** — never commit captured real sessions; they can
carry private repo internals into history.

## Status

Early. Core + `repetition`/`error_streak` detectors + Claude Code adapter +
Codex adapter + generic adapter. The transcript-tail supervisor is planned.

## License

Apache 2.0. See `LICENSE`.
