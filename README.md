<p align="center">
  <img src="https://raw.githubusercontent.com/nchantarotwong/hamingja/main/docs/assets/hamingja-mark.svg" width="112" alt="Hamingja: two interwoven strands forming an H inside an open circle">
</p>

# Hamingja

Fail-open partner rails for human–AI coding partnerships.

In Old Norse tradition, a *hamingja* is an accompanying guardian spirit and a
person's embodied good fortune—something that could be lent to help another
through a difficult undertaking. Hamingja brings that idea to Codex and Claude
Code: protection and accumulated project wisdom that support the partnership
without taking control away from either partner.

hamingja watches the tool-call stream for a small set of mechanically
testable non-convergence signatures, keeps resource checkpoints distinct from
those tripwires, and turns repetitive operational work into deterministic
wrappers. It is designed to help an agent recover and finish—not to judge
ordinary reasoning, enforce a preferred workflow, or stop work merely because
a session is long.

The shipped system has four cooperating layers:

- high-confidence tripwires for repetition, oscillation, and error streaks;
- advisory-by-default resource budgets grounded in observed progress, operator
  turns, and real quota signals when the runtime exposes them;
- recovery, delegation, and observe-mode audit state with bounded local data;
- soft instruction profiles and resumable PR/CI/navigation wrappers.

The core event model is harness-neutral. Codex and Claude Code are the tested
runtime promises; other loops can use the generic adapter on a best-effort
basis. Every uncertain or malformed guardrail-evaluation path fails open.

---

## Install

Python 3.13 or newer is required. For a global command used by every agent
session, `pipx` keeps hamingja isolated from project environments:

```bash
pipx install --python python3.13 hamingja
hamingja install           # installs for every harness it detects under ~/
# or, explicitly:
hamingja install claude    # Claude Code   (alias for claude_code)
hamingja install codex     # Codex
hamingja install all       # both, regardless of what's already set up
hamingja uninstall all     # remove only hamingja hooks; preserve others
```

`hamingja install` runs the bundled installer; with no argument it picks
up whatever it finds under `~/.claude/` and `~/.codex/`. The raw
`bash hamingja/adapters/<harness>/install.sh` still works if you'd rather
not install the package. After installing, `hamingja status` prints the
resolved config for any directory, `hamingja report` shows what has fired,
`hamingja locate` ranks small line ranges to inspect before reading large
files, and `hamingja init` generates a `CLAUDE.md` (+ `AGENTS.md` symlink)
for the project's soft workflow rails.

`hamingja uninstall claude|codex|all` removes only recognized hamingja
hook commands, preserves every unrelated hook and setting, writes a backup only
when it changes a file, and refuses to rewrite malformed configuration.

To upgrade, run `pipx upgrade hamingja` (or reinstall the editable checkout),
then `hamingja install all` to refresh hook paths. Claude and Codex processes
cache hook commands for the life of a session or application process; restart
running sessions after changing the Python installation or hook paths.

### Code locator

```bash
hamingja locate "pick directory endpoint"
hamingja locate-symbol "do_GET"
hamingja locate-edit "where should I add repo root field?"
hamingja code-atlas
hamingja repo-health
hamingja code-atlas --json       # versioned output; flags possible truncation
```

The locator is the generic fallback before reading a large file. `code-atlas`
prints a deterministic file map of symbol/section line ranges without file
contents; `locate` consults that map first, then combines ripgrep hits when
available with path/name relevance and simple block-boundary expansion. Both
paths print bounded line ranges and read commands.
`repo-health` surfaces large files, approximate unscoped-read token cost, and
language-matched split-name hints. Generated/build/vendor trees are excluded;
generated files elsewhere are labeled and receive no source-split advice.
Repo-specific helpers such as `refs.sh` can still be better
expert tools, but these commands are available everywhere.

### Ruled-out ledger

```bash
hamingja ledger add --kind ruled-out --claim "..." --evidence "..." --falsifier "..." --scope path/to/file
hamingja ledger check
hamingja ledger relevant path/to/file
hamingja ledger reverify <slug> [--timeout 60]
hamingja ledger retire <slug>
```

The ledger records negative knowledge in `.ledger/`: falsified hypotheses,
dead ends, and non-obvious constraints with evidence and file-hash expiry.
`ledger check` refreshes the one-line index and marks records `STALE` when a
pinned file has drifted. Malformed records are ignored rather than blocking
the workflow. The installed PreToolUse tripwire also surfaces relevant records
as advisory context before edit/write tools touch a scoped path.

### Claude Code

```bash
hamingja install claude
```

Merges six hooks into `~/.claude/settings.json` — a `PreToolUse` tripwire,
a recorder on both `PostToolUse` (success) and `PostToolUseFailure` (failure, so
error detection is by event, not by parsing an undocumented result shape), and
`SubagentStart` / `SubagentStop` lifecycle recorders, and a prompt-free
`UserPromptSubmit` operator-turn anchor. The
merge preserves your other hooks, is idempotent, self-heals a moved repo path,
and backs up only when it actually changes something. Default mode is `observe`,
so mechanical detector blocks are downgraded while you tune them. Explicit
operator resource boundaries remain separately configured.

### Codex

```bash
hamingja install codex
```

Merges five hooks into `~/.codex/hooks.json` — a `PreToolUse` tripwire, a
`PostToolUse` recorder, and `SubagentStart` / `SubagentStop` lifecycle
recorders, plus a prompt-free `UserPromptSubmit` operator-turn anchor. The
merge preserves your other hooks, is idempotent,
self-heals a moved repo path, and backs up only when it actually changes
something. Codex may ask you to review/trust the new hooks with `/hooks`.
Default mode is `observe`, so mechanical detector blocks are downgraded while
you tune them. Explicit operator resource boundaries remain separately
configured.

Codex hook coverage follows Codex's hook support: `PreToolUse` / `PostToolUse`
currently cover Bash, `apply_patch`, and MCP tools, but not every possible
tool path. In particular, newer shell execution paths may bypass tool hooks;
when Codex does not emit `PostToolUse`, hamingja cannot observe that result,
so the `error_streak` detector is best-effort for Codex. `repetition` still
works for any `PreToolUse`-covered call.

### Runtime capability boundary

| Capability | Claude Code | Codex |
|---|---|---|
| Pre-tool interception | full hook coverage | partial; supported tool paths only |
| Post-tool outcomes | success + explicit failure events | partial; only emitted hook paths |
| Child spawn, identity, completion | observed | observed |
| Parent-agent lineage | unavailable | unavailable |
| Operator-turn recency | observed without storing prompt text | observed without storing prompt text |
| Subscription quota percentage | unavailable; context occupancy only | available with bounded freshness |

Parent lineage is intentionally not inferred from session IDs, turn IDs,
timing, or transcript paths. Delegation depth therefore remains observe-only;
see [the lineage investigation](docs/delegation-lineage-investigation.md).

---

## The one rule: fail open

A guardrail that fails *closed* — blocking tool calls because of its own bug —
is worse than no guardrail, especially when installed globally across every
project. So **every layer here fails open**: any error (unparseable input,
unreadable state, a throwing detector, bad config) defaults to *allowing* the
call. The only things that ever block a call are an explicit, tested tripwire
or an explicit, tested operator authority boundary.

Fail-open here means “do not deny the agent's tool call because hamingja
failed.” Deterministic workflow wrappers still fail loudly and refuse an unsafe
merge when external CI or PR state is unknown; returning a wrapper error is not
the same as intercepting the caller's tool execution.

## What it detects

Ranked by signal quality (lowest false-positive rate first):

| Detector | Fires on | Why it's safe |
|---|---|---|
| `repetition` | the **same** normalized tool identity recurring | progress *varies* calls; nudges fire on literal repetition, while enforcement requires a complete payload plus repeated failure or identical substantive output |
| `leverage_fallback` | a configured semantic/freshness tool fails and the next command switches to weaker broad search over a configured protected target | inert until configured; catches fail-open bypasses without policing ordinary search |
| `oscillation` | a short **cycle** (period 2–4) repeating — flipping between the same handful of calls | requires ≥2 distinct calls in the cycle (pure repetition is left to `repetition`), and ≥2 full laps, so a couple of coincidental back-and-forths don't trip it |
| `error_streak` | consecutive errors with no success between | resets to zero on **any** success, so "failed → fixed → succeeded" never trips it |

Workflow/resource detectors such as `workflow_wrapper`, `read_discipline`, and
`python_command` are advisory-only. They can point toward a safer or cheaper
path, but cannot become mechanical denials merely because global mode is
`enforce`.

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

### Budget gate

The budget gate is a resource/workflow system, separate from mechanical
non-convergence tripwires. Its weighted-call checkpoint is advisory by default
and is emitted once per material checkpoint state. Trusted operator config may
set `budget.checkpoint_deny: true`; when denial is enabled, the message includes
the session id for the CLI recovery commands:

```bash
hamingja budget <session-id>          # show current counters
hamingja budget <session-id> add 20   # approve 20 more tool calls
hamingja budget <session-id> reset    # clear all counters for the session
hamingja budget <session-id> reset 20 # reset, then pre-approve 20 calls
hamingja budget <session-id> subagent # approve one subagent spawn
hamingja budget <session-id> add 3 --self  # agent self-approve, if config allows it
hamingja recover <session-id> handoff      # bounded fresh-session packet
hamingja recover <session-id> reset        # clear detector history; preserve audit
```

`reset` is the operator-stop recovery path: it deletes the session budget state so
the next tool call starts from fresh counters. `reset N` also grants runway
above the configured checkpoint for the resumed session.

Subagent spawns retain a conservative monotonic allowance
(`budget.max_subagents`, default 1) because lifecycle start hooks cannot deny a
spawn and neither runtime exposes parent-agent lineage. Once the allowance is spent,
`budget <session-id> subagent` grants exactly one additional spawn. Static
capability manifests keep this limitation explicit. Both runtimes now expose
stable child identity and completion events, so hamingja also tracks active
children at session scope and advises above `delegation.max_active_children`
(default 1). Inspect that state with `hamingja delegation <session-id>`.
If a runtime exits before emitting completion, clear stale advisory state with
`hamingja delegation <session-id> reset`.

#### Progress crediting

The budget counter is not a blind call counter — it meters *spend since
progress*. Observed verification credits the live counter back down, so a
session that keeps converging earns headroom and rarely checkpoints while a
stalled one re-checkpoints fast. This is what keeps the gate from punishing a
model that simply takes more, smaller tool calls: granular *productive* work
earns its calls back; granular *spinning* does not. Credit comes only from
outcomes the agent cannot fake from narration:

- a test or build going **red→green** (strongest),
- the reported **failure set shrinking** for the exact same validation,
- a real **error streak breaking** on a success,
- a **clean validation** run with nothing already failing (smallest).

Automatic red→green credit is scoped to the exact normalized validation
identity, so an unrelated green test cannot erase pressure from a different
failure. Adapters and deterministic workflow wrappers may submit richer
evidence with `record_progress()`. A claim earns credit only when its anchor
matches a recent recorded argument/output hash and its kind-specific transition
fields validate.

The Codex and Claude adapters extract bounded failure counts from standalone
pytest, unittest, Cargo, and Jest-family runs. They retain only the count and
normalized validation identity—never command text or test output. A zero count
is accepted only when the hook also proves the command succeeded.

Tool success alone, a novel command, or a self-asserted "done" earn nothing —
crediting those would refuel exactly the doom loop the budget exists to catch
(green-while-wrong is the most expensive failure class). Credit only ever
*lowers* pressure, and the `repetition` / `oscillation` / `error_streak`
detectors still block independently, so a false-positive credit cannot carry a
loop past them. Tune the credit magnitudes or disable the whole behavior under
`budget.progress` in config.

The same weighted counter discounts known cheap tools. Read-class tools cost
less than edits, and `hamingja ledger check` / `ledger relevant` are free
because they are guardrail orientation. `ledger add` and `ledger retire` cost a
small nonzero amount; `ledger reverify` stays full-cost because it can execute a
falsifier command.

#### Quota-aware gating (real subscription limits)

The call counter is a *proxy* for spend. On a Claude/Codex CLI subscription the
real scarce resource is the rate-limit window, not dollars-per-token — so where
the harness records its own quota, hamingja reads it and gates on the real
signal instead of the proxy. Each adapter ships a fail-open probe that tails the
harness's own session log; a missing or unreadable signal falls straight back to
the advisory call-count path. Missing, stale, malformed, or throwing probes
never imply scarcity and cannot arm a denial.

- **Codex** logs server-side rate limits in its session rollout: a 5-hour
  rolling window (`primary`) and a weekly cap (`secondary`) as a used-percent.
  When both are comfortably low, the proxy checkpoint is suppressed until
  material state changes. A fresh reading at or above the configured scarcity
  threshold can arm the separately configured operator stop, but only after its
  volume and stall predicates are also satisfied. The mechanical detectors
  still block independently. Tune checkpoint suppression with
  `budget.quota_relief_below_pct` (default 50; `0` disables).

The default operator stop is not an unconditional call ceiling. It requires
work beyond the configured hard ceiling and stall window, proven unattended
work, plus positive danger evidence: either a strong mechanical non-convergence
signature or fresh measured quota scarcity. Call volume alone only advises.
Trusted configuration may set
`budget.operator_stop.unconditional: true`; repository configuration may relax
or disable the stop but cannot tighten it. Codex and Claude `UserPromptSubmit`
hooks record only the current weighted-work counter—never prompt text—so a
recent operator intervention deterministically disarms the unattended branch.
Missing or malformed prompt events fail open and cannot arm a stop.

- **Claude Code** does not persist a rate-limit percent (it lives on API
  response headers), so its probe reports only **context occupancy** from the
  transcript's per-message `usage`. A nearly-full context window is re-sent every
  turn — a real cost even at a low call count — so hamingja emits a
  context-fill nudge past `budget.context_nudge_pct` (default 80; `0` disables).
  Occupancy is estimated against `budget.context_window_tokens` (default
  200000); an over-large real window only under-reports, never over-blocks.

Context-fill nudging applies to both harnesses (Codex reports occupancy too);
checkpoint relief applies only where the rate-limit signal exists (Codex today).

## Graduated response, not a kill switch

Configuration authority remains `off` / `observe` / `enforce`. Separately,
verdict metadata describes what kind of response the evidence warrants:

- **observe** — local audit only;
- **advise** — concise, non-blocking context;
- **checkpoint** — a bounded plan or recovery request, advisory unless trusted
  operator configuration explicitly allows denial;
- **tripwire** — deny one mechanically matched action while leaving recovery
  actions available;
- **operator_stop** — reserved for explicit operator resource boundaries and
  gated by configured stall, unattended, and danger evidence unless trusted
  configuration deliberately makes it unconditional.

Incomplete payloads, missing runtime observations, malformed state, and call
volume alone never justify a tripwire. Repetition enforcement requires strong
evidence such as repeated failures or identical substantive output.

A block must never **wedge** the agent. A denied call doesn't run, so it
produces no result — which means a detector keyed on outcomes (`error_streak`)
would otherwise keep denying *every* following call, including the very
diagnostic the block asks for, with no success ever recorded to clear the
streak. So a block **records itself** as a distinct event in the session
history: that marker breaks the error streak (the agent gets to act on the
diagnosis), while an identical *retry* still matches and stays blocked under
`repetition`. The block is an intervention, not a dead end.

## Modes (safe rollout)

* `observe` *(default)* — downgrades mechanical detector blocks to nudges
  carrying `would_block`, so you can tune thresholds against your real
  workflow. It does not implicitly disable separately configured operator
  resource boundaries.
* `enforce` — permits individually reviewed mechanical detectors to block;
  advisory-only workflow/resource detectors remain non-blocking.
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
repo-local `.hamingja.json` can downgrade a detector toward `observe` or
`off`, but cannot escalate it to `enforce`.

Observe mode only earns its keep if the would-blocks are visible, so every
non-allow verdict is appended to an audit log and `hamingja report` turns it
into a per-detector tuning summary:

```
$ hamingja report
hamingja report  (37 verdicts across 4 session(s))

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
often, then flip to `enforce`. `hamingja report --reset` clears the log;
`--json` emits the aggregate summary. Use `--since-hours HOURS` for a bounded
dogfood window so older audit records do not contaminate current tuning. The
summary includes response-shape counts and aggregate first/last timestamps,
but never prompts, commands, paths, tool output, or captured sessions.

**Per-repo opt-out:** drop a `.hamingja-off` file at the repo root and the
guard stands down there — recording goes inert too — for repos that
legitimately flail (long migrations, known-noisy tasks). It's honored even when
the agent runs in a subdirectory.

### Config & trust model

Configuration resolves in this order, and the trust boundary matters:

1. built-in defaults
2. packaged `hamingja/config.default.json` — **trusted** (ships with the install)
3. user-level `~/.hamingja/config.json` — **trusted** (operator-owned; may tighten)
4. matched user-level `~/.hamingja/policies/*.json` — **trusted** (operator-owned; may tighten)
5. per-project `.hamingja.json`, searched from the agent's cwd up to the
   repo root — **untrusted**: it may only *relax* the guard (raise thresholds,
   disable detectors, lower the window, downgrade mode toward `off`, or *extend*
   the read-only `exempt_tools` allowlist). It can **never** escalate to
   `enforce`, lower a threshold, or *remove* an exemption, so a hostile or
   careless repo cannot brick the agent by forcing its first tool call to be
   denied.
6. `.hamingja-off` marker (same upward search) → `off`
7. `HAMINGJA_MODE` env var — **trusted** (your shell); may set any mode.

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

Put that file at `~/.hamingja/policies/compiler-tools.json`. It can tighten
because it is trusted local operator state. The repo's own `.hamingja.json`
still cannot add those strict patterns; it can only relax thresholds or opt out.

For tests or isolated installs, set `HAMINGJA_HOME` to point at an alternate
trusted policy root.

---

## Architecture

```
hamingja/
  cli.py       report / status / install / init / workflow wrappers
  core/        events.py   normalized ToolEvent (the harness-neutral schema)
               state.py    session-keyed rolling log (locked, fail-open)
               engine.py   run enabled detectors -> aggregate -> verdict
               api.py      check()/record() — the one entry point adapters call
               budget.py   progress-aware operator resource state
               progress.py admitted observed/structured progress evidence
               delegation.py bounded child lifecycle state
               audit.py    verdict audit log behind observe mode (the report source)
  detectors/   base.py     Detector interface + Verdict   <-- HARD layer (can block)
               repetition.py, oscillation.py, error_streak.py
  config.py    config loading, trusted policy registry, trust model, sanitization
  config.default.json      packaged trusted defaults (ships in the wheel)
  adapters/    capabilities.py  versioned Codex/Claude observability manifests
               progress.py / framework_progress.py  anchored workflow/test evidence
               claude_code/  PreToolUse tripwire + PostToolUse recorder + install.sh
               codex/        PreToolUse tripwire + PostToolUse recorder + install.sh
               generic/      observe()/check() for any custom agent loop
  profiles/    base / non_convergence / debugging / ...   <-- SOFT layer (advisory)
  templates/   AGENTS.md / codex/AGENTS.md   installable header used by `init`
  workflows.py deterministic PR / CI / test-log wrappers
tests/         synthetic-sequence unit tests
```

The detector core is pure and harness-agnostic. Each supported adapter
translates native hook payloads into `ToolEvent` records and verdicts back into
the runtime's response shape. The generic `check()` / `observe()` adapter is
available for custom loops, but hamingja does not claim a transcript-tail
supervisor or complete enforcement for runtimes whose hooks omit tool paths.

---

## Soft workflow layer (profiles + `init`)

The mechanical tripwires above are the **hard** layer. Advisory detectors and
the reusable agent-facing workflow profiles sit beside them without acquiring
hidden enforcement authority. The profiles are deliberately off the hot path:

```
hamingja/profiles/   # pure markdown, no runtime
  base.md               progress = repro/narrow/shrink, not tokens
  non_convergence.md    user-says-stop -> review packet, no edits
  debugging.md          classify, repro, hypothesize, falsify before editing
  escalation.md         bounded sub-agent packet; stronger model only when needed
  review_passes.md      several bounded passes, not one giant pass
  compiler_language.md  opt-in: phase-based compiler/language work
```

`hamingja init` drops them into a project. The default writes
`./CLAUDE.md` and creates `./AGENTS.md` as a relative symlink to it, so
Claude Code (reads `CLAUDE.md` natively) and Codex (reads `AGENTS.md`) see
the same content with no second file to keep in sync:

```bash
hamingja init                                # ./CLAUDE.md + ./AGENTS.md -> CLAUDE.md
hamingja init --list                         # show available profiles
hamingja init --profile debugging,escalation # explicit profile set
hamingja init --force                        # regenerate, overwriting both files
hamingja init --no-link                      # CLAUDE.md only, no symlink
hamingja init --out AGENTS.md --no-link      # single AGENTS.md (no CLAUDE.md, no symlink)
hamingja init --out X.md --link Y.md         # custom pair: writes X, links Y -> X
hamingja init --dry-run                      # preview content + symlink plan; no writes
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
state, or scanning a pytest log for the same failure header. `hamingja`
therefore ships small local wrappers that produce one clean artifact for the
agent to reason over:

```bash
hamingja commands                  # discover global + repo-local wrappers first
hamingja pr-create --title "..." --body-file pr.md
hamingja pr-create --title "..." --body-file pr.md --json
hamingja pr-create --title "..." --body - < pr.md  # stdin body, temp file, then gh --body-file
hamingja pr-merge 123              # wait for CI checks, gh merge + wait for MERGED + local cleanup
hamingja pr-merge 123 --json       # versioned merged/blocked/failed/interrupted state
hamingja pr-merge 123 --skip-ci-reason "GHA budget exhausted; local suite passed"
hamingja post-merge-cleanup topic  # checkout main, pull --ff-only, branch -d topic
hamingja post-merge-cleanup topic --force-delete  # for squash/rebase-cleaned branches
hamingja ci-status 123             # compact PR check summary; flags Actions budget/quota blocks
hamingja ci-status 123 --wait      # poll with backoff until checks finish or timeout
hamingja ci-status 123 --json      # versioned ready/pending/failed/blocked state
hamingja ci-preflight 123          # classify CI quota/infrastructure readiness before reruns
hamingja ci-failures 123           # shorthand for --pr 123
hamingja ci-failures --pr 123      # failed-run log summary for the PR branch
hamingja ci-failures --run 456     # failed-run log summary for a run id
hamingja test-summary .pytest_output.log
hamingja preflight --list          # list repo-owned readiness checks
hamingja preflight full-suite-readiness
```

Workflow JSON includes `resumable` and a bounded `next_action`. Interrupted or
partially completed PR operations can be rerun safely: create rechecks branch
and upstream state, while merge rechecks the remote PR state before attempting
another merge. Direct Python callers receive the same lifecycle metadata on
failure as well as success.

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
checks, `pr-merge` first checks PR mergeability so branch conflicts are
reported directly instead of being misclassified as missing CI. When local repo
inspection proves there are no `.github/workflows/*.yml` or `*.yaml` files, it
treats the repo as no-CI and skips the gate without an extra failed merge
attempt.

`ci-status` and `ci-preflight` classify failed Action runs that look like
quota/infrastructure blocks, including runs where every failed job completed
before any job steps were recorded. `ci-status --wait` polls with backoff so an
agent does not burn repeated tool calls manually checking pending jobs.

`ci-failures` summarizes pytest-style failures from both stdout and stderr of
`gh run view --log-failed`, including GitHub timestamp-prefixed and ANSI-colored
pytest summary lines. If failed logs are unavailable, it falls back to run
metadata and reports no-step CI infrastructure/budget failures directly.

The rule for project instructions is simple: run `hamingja commands` before
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

### Repo-local preflights

Some expensive validation loops are repo-specific: one project may need a
compiler freshness check before its full suite, while another may need schema
drift detection or generated-artifact checks. Keep that policy in the repo, not
in hamingja, by adding executable scripts under `.hamingja/preflight/`:

```
myrepo/
  .hamingja/
    preflight/
      full-suite-readiness
```

`hamingja preflight` lists available scripts, and
`hamingja preflight <name>` runs the selected script from the repo root. The
runner passes arguments through after `--`, sets `HAMINGJA_REPO_ROOT` and
`HAMINGJA_PREFLIGHT_NAME`, and returns the script's exit code unchanged.
Use exit `0` for ready, `1` for a blocking repo finding, and `2` for usage or
environment failures.

This keeps hamingja generic: it owns discovery, path hygiene, argument
forwarding, and consistent command shape. The target repo owns what "ready"
means.

### Use from another repo

Install hamingja once from its own checkout, then use it across all of your
coding-agent repos. The hamingja checkout does **not** need to live inside
each repo; a common sibling layout is enough:

```
~/dev/
  hamingja/        # checked out once
  myrepo/               # your language/compiler repo
  other-repo/
```

From the hamingja checkout:

```bash
cd ~/dev/hamingja
pipx install --python python3.13 --editable .
hamingja install       # installs for whichever of claude_code / codex is on this machine
```

That global install is the mechanical layer: it puts the CLI on PATH and
merges hooks into `~/.claude/settings.json` and/or `~/.codex/hooks.json`. The
hooks resolve the installed package, so every repo gets the guardrail without
vendoring hamingja.

Each target repo only needs local configuration and instructions:

```
myrepo/
  .hamingja.json   # optional guardrail thresholds / mode relaxations
  CLAUDE.md           # generated soft workflow instructions
  AGENTS.md -> CLAUDE.md
```

One command for the per-repo instructions. A first `hamingja init` uses the
default profiles. Re-running without `--profile` preserves the profile set in
the existing managed block, including opt-ins like `compiler_language`.
`--profile` is still explicit: it writes exactly the set you pass.

```bash
cd ~/dev/myrepo
hamingja init                                                      # default profiles
# or, with the opt-in compiler-language profile:
hamingja init --force --profile base,non-convergence,debugging,escalation,review-passes,compiler-language
```

Either form writes `./CLAUDE.md` and drops `./AGENTS.md` as a relative
symlink. If `AGENTS.md` already exists as a real file, `init` refuses; pass
`--force` only after preserving anything you still need from it.

Use `.hamingja.json` only for the hard guardrail runtime config. Project
config is untrusted and may only relax the installed baseline: raise thresholds,
disable detectors, lower `window`, downgrade `mode` toward `off`, or extend the
read-only exemption list. It cannot force `enforce` or lower thresholds.

Use `~/.hamingja/policies/*.json` for repo-specific strict rules, such as
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
by `hamingja init`, so changing the profile set means regenerating or
editing the file.

Run in `observe` first, then inspect what would have fired:

```bash
hamingja report
```

When you are ready to enforce blocks, set trusted mode from your shell:

```bash
HAMINGJA_MODE=enforce claude
```

Use `.hamingja-off` at a repo root to stand the guard down completely for
that repo.

---

## Use in your own agent loop

```python
from hamingja.adapters.generic import observe, check
from hamingja.core.api import record_progress

verdict = check(session_id, tool_name, tool_args)   # before the call
if verdict.action == "block":
    ...                                              # re-plan; verdict.reason says why
observe(session_id, tool_name, tool_args, ok=succeeded)  # after the call

# Optional adapter/workflow evidence; anchor must match a recent event hash.
record_progress(session_id, {
    "kind": "failure_set_shrank",
    "anchor": observed_event_hash,
    "validation_id": "pytest:targeted",
    "failure_count_before": 12,
    "failure_count_after": 3,
})
```

The Claude Code and Codex adapters also recognize standalone pytest,
unittest, Cargo, and Jest-family test commands. They retain only bounded
failure counts keyed by the observed command hash and credit progress when the
same validation's reported failure count shrinks; command text and test output
are not persisted.

## Tests

```bash
python -m pytest          # if pytest is installed
python tests/test_detectors.py   # or run any test file directly
```

Fixtures are **synthetic only** — never commit captured real sessions; they can
carry private repo internals into history.

---

## Status

The Codex + Claude partner-rails rescope is complete. The public compatibility
promise covers the harness-neutral core, installed Codex and Claude hooks,
observe-mode audit/recovery, progress-aware operator budgets, bounded child
lifecycle advisories, navigation, and deterministic workflow wrappers. The
suite uses synthetic fixtures only.

The architectural decisions and implementation record live in the
[completed rescope document](docs/codex-claude-partner-rails-rescope.md).
Release preparation is documented in the
[release checklist](docs/release-checklist.md); notable changes are collected
in [CHANGELOG.md](CHANGELOG.md).

Known boundaries are explicit: Codex hook interception is partial; Claude does
not expose subscription quota percentages; neither inline hook contract exposes
parent-agent lineage; and hamingja does not infer semantic correctness or
police ordinary reasoning. Those gaps fail open rather than being filled with
timing, transcript, or payload guesses.

## License

Apache 2.0. See `LICENSE`.
