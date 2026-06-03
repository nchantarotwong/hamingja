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
| `oscillation` | a short **cycle** (period 2–4) repeating — flipping between the same handful of calls | requires ≥2 distinct calls in the cycle (pure repetition is left to `repetition`), and ≥2 full laps, so a couple of coincidental back-and-forths don't trip it |
| `error_streak` | consecutive errors with no success between | resets to zero on **any** success, so "failed → fixed → succeeded" never trips it |

`repetition` and `oscillation` **exempt read-only / idempotent tools**
(`Read`, `Grep`, `Glob`, `LS`, `WebFetch`, …): re-reading a file or re-running
a query is normal, not flailing. The exempt list is configurable and a project
may only *extend* it (see below). `error_streak` still applies to those tools —
a read that keeps *erroring* is still a stuck loop.

Add a guardrail = a new file in `detectors/` implementing the `Detector`
interface, registered in `core/engine.py`.

## Graduated response, not a kill switch

* **nudge** — inject an advisory into the agent's context ("3rd identical call;
  change approach"). Non-blocking. This is just forcing the
  state-then-hypothesis discipline at the right moment.
* **block** — deny the call. A hard block is the only thing that reliably
  interrupts the loop, because injected text can be ignored by a poisoned
  context but a denied call cannot.

A block must never **wedge** the agent. A denied call doesn't run, so it
produces no result — which means a detector keyed on outcomes (`error_streak`)
would otherwise keep denying *every* following call, including the very
diagnostic the block asks for, with no success ever recorded to clear the
streak. So a block **records itself** as a distinct event in the session
history: that marker breaks the error streak (the agent gets to act on the
diagnosis), while an identical *retry* still matches and stays blocked under
`repetition`. The block is an intervention, not a dead end.

## Modes (safe rollout)

* `observe` *(default)* — never blocks; emits a nudge carrying `would_block`, so
  you can tune thresholds against your real workflow before enforcing.
* `enforce` — blocks for real.
* `off` — disabled.

Observe mode only earns its keep if the would-blocks are visible, so every
non-allow verdict is appended to an audit log and `agent-rails report` turns it
into a per-detector tuning summary:

```
$ agent-rails report
agent-rails report  (37 verdicts across 4 session(s))

  nudges:        21
  would-block:   14   (these become BLOCKS under enforce)
  blocks:         2   (already enforced)

  detector           nudge   would-block   block
  ---------------- ------- ------------- -------
  repetition             9             8       1
  oscillation            7             5       0
  error_streak           5             1       1
```

Run observe for a while, read the report, raise any threshold that fires too
often, then flip to `enforce`. `agent-rails report --reset` clears the log;
`--json` emits the raw summary.

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
   disable detectors, lower the window, downgrade mode toward `off`, or *extend*
   the read-only `exempt_tools` allowlist). It can **never** escalate to
   `enforce`, lower a threshold, or *remove* an exemption, so a hostile or
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
  cli.py       agent-rails report / status / install / init — operator-facing CLI
  core/        events.py   normalized ToolEvent (the harness-neutral schema)
               state.py    session-keyed rolling log (locked, fail-open)
               engine.py   run enabled detectors -> aggregate -> verdict
               api.py      check()/record() — the one entry point adapters call
               audit.py    verdict audit log behind observe mode (the report source)
  detectors/   base.py     Detector interface + Verdict   <-- HARD layer (can block)
               repetition.py, oscillation.py, error_streak.py
  config.py    config loading, trust model, sanitization
  config.default.json      packaged trusted defaults (ships in the wheel)
  adapters/    claude_code/  PreToolUse tripwire + PostToolUse recorder + install.sh
               codex/        PreToolUse tripwire + PostToolUse recorder + install.sh
               generic/      observe()/check() for any custom agent loop
  profiles/    base / non_convergence / debugging / ...   <-- SOFT layer (advisory)
  templates/   AGENTS.md / CLAUDE.md / codex/AGENTS.md     installable headers
tests/         synthetic-sequence unit tests
```

The detector core is pure and harness-agnostic. Each adapter only translates a
harness's native payload into a `ToolEvent` and a verdict back into that
harness's response. Two ingestion modes are supported by design:

1. **Inline hook** (synchronous, *can block*) — e.g. the Claude Code adapter.
2. **Transcript-tail / supervisor** (asynchronous, *observe + kill + notify*) —
   the universal fallback for harnesses without pre-call hooks, and the basis
   for an at-scale fleet watchdog. *(planned)*

## Install

```bash
pip install -e .              # or: pip install agent-rails (once published)
agent-rails install claude    # Claude Code   (alias for claude_code)
agent-rails install codex     # Codex
```

`agent-rails install` runs the bundled installer for that harness; the raw
`bash agent_rails/adapters/<harness>/install.sh` still works if you'd rather not
install the package. After installing, `agent-rails status` prints the resolved
config for any directory, and `agent-rails report` shows what has fired.

### Claude Code

```bash
agent-rails install claude
```

Merges three hooks into `~/.claude/settings.json` — a `PreToolUse` tripwire and
a recorder on both `PostToolUse` (success) and `PostToolUseFailure` (failure, so
error detection is by event, not by parsing an undocumented result shape). The
merge preserves your other hooks, is idempotent, self-heals a moved repo path,
and backs up only when it actually changes something. Default mode is `observe`,
so nothing is blocked until you flip to `enforce`.

### Codex

```bash
agent-rails install codex
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

## Soft workflow layer (profiles + `init`)

The detectors above are the **hard** layer: deterministic, mechanical, can
block. Sitting next to them — and deliberately off the hot path — is a
**soft** layer of reusable agent-facing workflow rails:

```
agent_rails/profiles/   # pure markdown, no runtime
  base.md               progress = repro/narrow/shrink, not tokens
  non_convergence.md    user-says-stop -> review packet, no edits
  debugging.md          classify, repro, hypothesize, falsify before editing
  escalation.md         default fast model; escalate only with a bounded packet
  review_passes.md      several bounded passes, not one giant pass
  compiler_language.md  opt-in: phase-based compiler/language work
```

`agent-rails init` composes these into an `AGENTS.md` for a project:

```bash
agent-rails init                          # ./AGENTS.md with the default profile set
agent-rails init --list                   # show available profiles
agent-rails init --profile debugging,escalation
agent-rails init --profile compiler-language --out AGENTS.md --force
agent-rails init --dry-run                # preview without writing
```

Profiles are markdown read **only at `init`-time** — they don't load at hook
time, don't get parsed by detectors, and can't affect the fail-open trust
model. They're advisory, not enforced. The blocking still comes from the
detectors; this layer is documentation that ships with the package so projects
have one less thing to write from scratch.

For Claude Code, drop a `CLAUDE.md` next to `AGENTS.md` that points at it (a
ready-made pointer template ships at `agent_rails/templates/CLAUDE.md`);
Codex reads `AGENTS.md` directly.

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

Early. Core + `repetition`/`oscillation`/`error_streak` detectors + read-only
exemption + verdict audit log & `agent-rails report` + Claude Code, Codex, and
generic adapters. The transcript-tail supervisor is planned.

Two detectors from the roadmap are deliberately **not** shipped yet, to keep the
"lowest false-positive first" promise intact:

* **near-duplicate by target** (same tool hammering the same file with slightly
  varied args) — iterative editing of one file is legitimate, so a naive
  version false-positives; it needs a target signature on the event and a
  careful threshold before it's safe to enable by default.
* **same-error-message recurrence** — high signal, but it depends on the
  per-tool result payload, whose shape is undocumented; agent-rails detects
  errors *by event* precisely to avoid parsing that. Adding it means committing
  to a payload heuristic.

## License

Apache 2.0. See `LICENSE`.
