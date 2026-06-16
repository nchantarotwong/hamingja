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

Alongside those detectors, agent-rails also ships a soft layer of reusable
workflow profiles — markdown rails for debugging, escalation, non-convergence,
and review passes — that you drop into a project with `agent-rails init`. The
detectors block; the profiles guide.

---

## Install

```bash
pip install -e .              # or: pip install agent-rails (once published)
agent-rails install           # installs for every harness it detects under ~/
# or, explicitly:
agent-rails install claude    # Claude Code   (alias for claude_code)
agent-rails install codex     # Codex
agent-rails install all       # both, regardless of what's already set up
```

`agent-rails install` runs the bundled installer; with no argument it picks
up whatever it finds under `~/.claude/` and `~/.codex/`. The raw
`bash agent_rails/adapters/<harness>/install.sh` still works if you'd rather
not install the package. After installing, `agent-rails status` prints the
resolved config for any directory, `agent-rails report` shows what has fired,
`agent-rails locate` ranks small line ranges to inspect before reading large
files, and `agent-rails init` generates a `CLAUDE.md` (+ `AGENTS.md` symlink)
for the project's soft workflow rails.

### Code locator

```bash
agent-rails locate "pick directory endpoint"
agent-rails locate-symbol "do_GET"
agent-rails locate-edit "where should I add repo root field?"
agent-rails code-atlas
agent-rails repo-health
```

The locator is the generic fallback before reading a large file. `code-atlas`
prints a deterministic file map of symbol/section line ranges without file
contents; `locate` consults that map first, then combines ripgrep hits when
available with path/name relevance and simple block-boundary expansion. Both
paths print bounded line ranges and read commands.
`repo-health` surfaces large files, approximate unscoped-read token cost, and
split-name hints. Repo-specific helpers such as `refs.sh` can still be better
expert tools, but these commands are available everywhere.

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

---

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
| `repetition` | the **same** normalized tool identity recurring | progress *varies* calls; nudges fire on literal repetition, while enforcement requires a complete payload plus repeated failure or identical substantive output |
| `leverage_fallback` | a configured semantic/freshness tool fails and the next command switches to weaker broad search over a configured protected target | inert until configured; catches fail-open bypasses without policing ordinary search |
| `oscillation` | a short **cycle** (period 2–4) repeating — flipping between the same handful of calls | requires ≥2 distinct calls in the cycle (pure repetition is left to `repetition`), and ≥2 full laps, so a couple of coincidental back-and-forths don't trip it |
| `error_streak` | consecutive errors with no success between | resets to zero on **any** success, so "failed → fixed → succeeded" never trips it |

`repetition` and `oscillation` **exempt read-only / idempotent tools**
(`Read`, `Grep`, `Glob`, `LS`, `WebFetch`, …): re-reading a file or re-running
a query is normal, not flailing. The exempt list is configurable and a project
may only *extend* it (see below). `error_streak` still applies to those tools —
a read that keeps *erroring* is still a stuck loop. Bash is normalized separately:
shell commands get a short preview and kind (`shell:read-only`, `shell:test`,
`shell:build`, `shell:mutating`, etc.). Repeated read-only shell diagnostics
stay quiet unless repeated failures or identical substantive output prove they
are returning the same signal; repeated test and build/rebuild shell commands
stay quiet before the block threshold without that evidence.

`leverage_fallback` is deliberately not a broad "grep is bad" rule. It only
fires when a configured leverage tool has just failed (or is embedded in the
same `|| grep`-style command) and the fallback targets a configured protected
surface. The packaged default leaves the project-specific pattern lists empty,
so the detector is inert until an operator adds trusted patterns for their
semantic navigator, freshness guard, generated-artifact validator, or similar
leverage tool.

Add a guardrail = a new file in `detectors/` implementing the `Detector`
interface, registered in `core/engine.py`.

## Graduated response, not a kill switch

* **nudge** — inject an advisory into the agent's context ("3rd identical call;
  change approach"). Non-blocking. This is just forcing the
  state-then-hypothesis discipline at the right moment.
* **block** — deny the call. A hard block is the only thing that reliably
  interrupts the loop, because injected text can be ignored by a poisoned
  context but a denied call cannot. Repetition blocks are deliberately stricter
  than repetition nudges: incomplete tool payloads are not enforceable, and a
  repeat needs strong evidence such as repeated errors or identical substantive
  output.

A block must never **wedge** the agent. A denied call doesn't run, so it
produces no result — which means a detector keyed on outcomes (`error_streak`)
would otherwise keep denying *every* following call, including the very
diagnostic the block asks for, with no success ever recorded to clear the
streak. So a block **records itself** as a distinct event in the session
history: that marker breaks the error streak (the agent gets to act on the
diagnosis), while an identical *retry* still matches and stays blocked under
`repetition`. The block is an intervention, not a dead end.

## Modes (safe rollout)

* `observe` *(default)* — never blocks globally; emits a nudge carrying
  `would_block`, so you can tune thresholds against your real workflow before
  enforcing.
* `enforce` — blocks for real.
* `off` — disabled.

Detector-level `mode` overrides the global mode. That lets you keep broad,
behavioral detectors in `observe` while enforcing a narrow, high-signal local
policy:

```json
{
  "mode": "observe",
  "detectors": {
    "leverage_fallback": {
      "mode": "enforce",
      "required_patterns": ["semantic-nav"],
      "protected_targets": ["src/compiler/main.lang"]
    },
    "repetition": {
      "mode": "observe"
    }
  }
}
```

As with thresholds, detector-level modes only tighten from trusted config. A
repo-local `.agent-rails.json` can downgrade a detector toward `observe` or
`off`, but cannot escalate it to `enforce`.

Observe mode only earns its keep if the would-blocks are visible, so every
non-allow verdict is appended to an audit log and `agent-rails report` turns it
into a per-detector tuning summary:

```
$ agent-rails report
agent-rails report  (37 verdicts across 4 session(s))

  nudges:        21
  would-block:   14   (become BLOCKS when the relevant mode is enforce)
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
3. user-level `~/.agent-rails/config.json` — **trusted** (operator-owned; may tighten)
4. matched user-level `~/.agent-rails/policies/*.json` — **trusted** (operator-owned; may tighten)
5. per-project `.agent-rails.json`, searched from the agent's cwd up to the
   repo root — **untrusted**: it may only *relax* the guard (raise thresholds,
   disable detectors, lower the window, downgrade mode toward `off`, or *extend*
   the read-only `exempt_tools` allowlist). It can **never** escalate to
   `enforce`, lower a threshold, or *remove* an exemption, so a hostile or
   careless repo cannot brick the agent by forcing its first tool call to be
   denied.
6. `.agent-rails-off` marker (same upward search) → `off`
7. `AGENT_RAILS_MODE` env var — **trusted** (your shell); may set any mode.

All values are sanitized: modes are canonicalized, and `window`/`block_at`/
`nudge_at`/detector-specific thresholds are coerced to ints with safe floors,
so a typo or out-of-range value can neither crash a detector nor cause a
spurious block.

The user-level policy registry is how private or project-specific guardrails
stay out of the public package while still becoming real hard protections. A
policy file matches by repo path or git remote and then contributes trusted
config:

```json
{
  "id": "compiler-tools",
  "match": {
    "repo_paths": ["/Users/me/src/compiler-project"],
    "repo_remotes": ["git@example.com:org/compiler-project"]
  },
  "detectors": {
    "leverage_fallback": {
      "required_patterns": ["semantic-nav", "schema-check"],
      "mode": "enforce",
      "protected_targets": ["src/compiler/main.lang", "generated/schema.json"]
    }
  }
}
```

Put that file at `~/.agent-rails/policies/compiler-tools.json`. It can tighten
because it is trusted local operator state. The repo's own `.agent-rails.json`
still cannot add those strict patterns; it can only relax thresholds or opt out.

For tests or isolated installs, set `AGENT_RAILS_HOME` to point at an alternate
trusted policy root.

---

## Architecture

```
agent_rails/
  cli.py       report / status / install / init / workflow wrappers
  core/        events.py   normalized ToolEvent (the harness-neutral schema)
               state.py    session-keyed rolling log (locked, fail-open)
               engine.py   run enabled detectors -> aggregate -> verdict
               api.py      check()/record() — the one entry point adapters call
               audit.py    verdict audit log behind observe mode (the report source)
  detectors/   base.py     Detector interface + Verdict   <-- HARD layer (can block)
               repetition.py, oscillation.py, error_streak.py
  config.py    config loading, trusted policy registry, trust model, sanitization
  config.default.json      packaged trusted defaults (ships in the wheel)
  adapters/    claude_code/  PreToolUse tripwire + PostToolUse recorder + install.sh
               codex/        PreToolUse tripwire + PostToolUse recorder + install.sh
               generic/      observe()/check() for any custom agent loop
  profiles/    base / non_convergence / debugging / ...   <-- SOFT layer (advisory)
  templates/   AGENTS.md / codex/AGENTS.md   installable header used by `init`
  workflows.py deterministic PR / CI / test-log wrappers
tests/         synthetic-sequence unit tests
```

The detector core is pure and harness-agnostic. Each adapter only translates a
harness's native payload into a `ToolEvent` and a verdict back into that
harness's response. Two ingestion modes are supported by design:

1. **Inline hook** (synchronous, *can block*) — e.g. the Claude Code adapter.
2. **Transcript-tail / supervisor** (asynchronous, *observe + kill + notify*) —
   the universal fallback for harnesses without pre-call hooks, and the basis
   for an at-scale fleet watchdog. *(planned)*

---

## Soft workflow layer (profiles + `init`)

The detectors above are the **hard** layer: deterministic, mechanical, can
block. Sitting next to them — and deliberately off the hot path — is a
**soft** layer of reusable agent-facing workflow rails:

```
agent_rails/profiles/   # pure markdown, no runtime
  base.md               progress = repro/narrow/shrink, not tokens
  non_convergence.md    user-says-stop -> review packet, no edits
  debugging.md          classify, repro, hypothesize, falsify before editing
  escalation.md         bounded sub-agent packet; stronger model only when needed
  review_passes.md      several bounded passes, not one giant pass
  compiler_language.md  opt-in: phase-based compiler/language work
```

`agent-rails init` drops them into a project. The default writes
`./CLAUDE.md` and creates `./AGENTS.md` as a relative symlink to it, so
Claude Code (reads `CLAUDE.md` natively) and Codex (reads `AGENTS.md`) see
the same content with no second file to keep in sync:

```bash
agent-rails init                                # ./CLAUDE.md + ./AGENTS.md -> CLAUDE.md
agent-rails init --list                         # show available profiles
agent-rails init --profile debugging,escalation # explicit profile set
agent-rails init --force                        # regenerate, overwriting both files
agent-rails init --no-link                      # CLAUDE.md only, no symlink
agent-rails init --out AGENTS.md --no-link      # single AGENTS.md (no CLAUDE.md, no symlink)
agent-rails init --out X.md --link Y.md         # custom pair: writes X, links Y -> X
agent-rails init --dry-run                      # preview content + symlink plan; no writes
```

Profiles are markdown read **only at `init`-time** — they don't load at hook
time, don't get parsed by detectors, and can't affect the fail-open trust
model. They're advisory, not enforced. The blocking still comes from the
detectors; this layer is documentation that ships with the package so projects
have one less thing to write from scratch.

---

## Workflow wrappers

Some agent waste is not a guardrail problem; it is deterministic workflow glue.
An LLM should not spend repeated tool calls polling GitHub, re-checking branch
state, or scanning a pytest log for the same failure header. `agent-rails`
therefore ships small local wrappers that produce one clean artifact for the
agent to reason over:

```bash
agent-rails commands                  # discover global + repo-local wrappers first
agent-rails pr-create --title "..." --body-file pr.md
agent-rails pr-create --title "..." --body - < pr.md  # stdin body, temp file, then gh --body-file
agent-rails pr-merge 123              # wait for CI checks, gh merge + wait for MERGED + local cleanup
agent-rails pr-merge 123 --skip-ci-reason "GHA budget exhausted; local suite passed"
agent-rails post-merge-cleanup topic  # checkout main, pull --ff-only, branch -d topic
agent-rails post-merge-cleanup topic --force-delete  # for squash/rebase-cleaned branches
agent-rails ci-status 123             # compact PR check summary; flags Actions budget/quota blocks
agent-rails ci-failures 123           # shorthand for --pr 123
agent-rails ci-failures --pr 123      # failed-run log summary for the PR branch
agent-rails ci-failures --run 456     # failed-run log summary for a run id
agent-rails test-summary .pytest_output.log
```

`pr-create` checks the current branch when `--head` is omitted. If the branch
has no upstream, it runs `git push -u origin HEAD:refs/heads/<branch>` before
calling `gh pr create`; use `--remote <name>` to push somewhere other than
`origin`. If the branch already has an upstream and local commits are ahead of
it, `pr-create` runs a non-force push to that configured upstream before
calling `gh pr create`. It refuses detached HEAD, the base branch, and diverged
upstream state so a PR is not accidentally opened from `main` or stale branch
state.

`pr-merge` gates on CI before merging: it polls the PR's checks (up to
`--ci-timeout`, default 1800s) and refuses to merge while any check is
failing, pending, unreported, or unreadable — branch protection in repos
that don't have server-side required checks. The gate is fail-closed; the
only bypass is an explicit, auditable `--skip-ci-reason`. If GitHub reports no
checks and local repo inspection proves there are no `.github/workflows/*.yml`
or `*.yaml` files, `pr-merge` treats the repo as no-CI and skips the gate
without an extra failed attempt.

`ci-failures` summarizes pytest-style failures from both stdout and stderr of
`gh run view --log-failed`, including GitHub timestamp-prefixed and ANSI-colored
pytest summary lines.

The rule for project instructions is simple: run `agent-rails commands` before
PR creation/merge/cleanup, CI status/failure extraction, or saved test-log
summary tasks. Use the listed wrapper before any manual recipe, then spend
judgment on the summarized result. Manual `gh`/`git` polling and cleanup steps
are fallback behavior only when the wrapper is unavailable or fails loudly. If
you use a raw fallback, say which wrapper was unavailable or failed.

Wrappers treat external CLI output as untrusted even when the command exits 0.
Malformed JSON shape, missing required fields, ambiguous PR/run selectors, and
oversized stdin bodies produce concise wrapper errors instead of tracebacks or
unscoped fallback behavior.

Codex sandbox note: GitHub wrappers usually need network and cleanup wrappers
may need `.git` writes. The Codex hook cannot upgrade sandbox permissions from
inside a subprocess, so it nudges before these wrappers run; rerun the wrapper
with sandbox escalation instead of first letting it fail in the sandbox.

### Use from another repo

Install agent-rails once from its own checkout, then use it across all of your
coding-agent repos. The agent-rails checkout does **not** need to live inside
each repo; a common sibling layout is enough:

```
~/dev/
  agent-rails/        # checked out once
  myrepo/               # your language/compiler repo
  other-repo/
```

From the agent-rails checkout:

```bash
cd ~/dev/agent-rails
pip install -e .          # or: pipx install -e .
agent-rails install       # installs for whichever of claude_code / codex is on this machine
```

That global install is the mechanical layer: it puts the CLI on PATH and
merges hooks into `~/.claude/settings.json` and/or `~/.codex/hooks.json`. The
hooks resolve the installed package, so every repo gets the guardrail without
vendoring agent-rails.

Each target repo only needs local configuration and instructions:

```
myrepo/
  .agent-rails.json   # optional guardrail thresholds / mode relaxations
  CLAUDE.md           # generated soft workflow instructions
  AGENTS.md -> CLAUDE.md
```

One command for the per-repo instructions. A first `agent-rails init` uses the
default profiles. Re-running without `--profile` preserves the profile set in
the existing managed block, including opt-ins like `compiler_language`.
`--profile` is still explicit: it writes exactly the set you pass.

```bash
cd ~/dev/myrepo
agent-rails init                                                      # default profiles
# or, with the opt-in compiler-language profile:
agent-rails init --force --profile base,non-convergence,debugging,escalation,review-passes,compiler-language
```

Either form writes `./CLAUDE.md` and drops `./AGENTS.md` as a relative
symlink. If `AGENTS.md` already exists as a real file, `init` refuses; pass
`--force` only after preserving anything you still need from it.

Use `.agent-rails.json` only for the hard guardrail runtime config. Project
config is untrusted and may only relax the installed baseline: raise thresholds,
disable detectors, lower `window`, downgrade `mode` toward `off`, or extend the
read-only exemption list. It cannot force `enforce` or lower thresholds.

Use `~/.agent-rails/policies/*.json` for repo-specific strict rules, such as
"if this semantic tool fails, do not fall back to broad text search over that
protected file." Those policies are trusted local operator state and can tighten
detectors, including setting only that detector to `enforce`, without putting
private repo names or paths in this public package.

Example repo-local config:

```json
{
  "mode": "observe",
  "detectors": {
    "repetition": {
      "nudge_at": 3,
      "block_at": 5
    },
    "oscillation": {
      "nudge_at": 4,
      "block_at": 7
    },
    "error_streak": {
      "nudge_at": 4,
      "block_at": 8
    },
    "read_discipline": {
      "block_first_read_at_lines": 1000
    }
  }
}
```

Profiles are **not** runtime config. They are markdown copied into `CLAUDE.md`
by `agent-rails init`, so changing the profile set means regenerating or
editing the file.

Run in `observe` first, then inspect what would have fired:

```bash
agent-rails report
```

When you are ready to enforce blocks, set trusted mode from your shell:

```bash
AGENT_RAILS_MODE=enforce claude
```

Use `.agent-rails-off` at a repo root to stand the guard down completely for
that repo.

---

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

---

## Status

Early. Core + `repetition`/`oscillation`/`error_streak` detectors + read-only
exemption + verdict audit log & `agent-rails report` + Claude Code, Codex, and
generic adapters + soft workflow profile layer & `agent-rails init` (writes
`CLAUDE.md`, symlinks `AGENTS.md`). The transcript-tail supervisor is planned.

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
