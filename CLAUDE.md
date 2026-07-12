# CLAUDE.md

You are working on **hamingja** — a small Python library that ships fail-open
partner rails for Codex and Claude Code. Its core event/detector model remains
harness-neutral, but Codex and Claude are the tested runtime promises; other
loops use the generic adapter on a best-effort basis. It installs globally and
runs inline on tool calls, so correctness and safety dominate. Optimize for
changes that are **easy to verify and hard to get subtly wrong**.

The product is broader than a circuit breaker: it combines mechanical
non-convergence tripwires, advisory-by-default operator resource budgets,
bounded recovery/delegation/audit state, and deterministic navigation and
workflow wrappers. Do not blur their authority boundaries.

Read `README.md` first if you haven't already; it carries the current product
model, runtime capability table, trust boundary, and graduated responses. The
completed architectural record is
`docs/codex-claude-partner-rails-rescope.md`. Treat documented capability gaps
as intentional until released wire contracts and fixtures prove otherwise.

## The hard rule: fail open

Every guardrail-evaluation layer fails open. The only things that may deny a
tool call are an **explicit, tested mechanical tripwire** or an **explicit,
tested operator authority boundary**. Anything else — exceptions, missing
files, unparseable JSON, bad types, out-of-range ints, throwing detectors,
missing runtime observations, bad config — must default to *allow*.

Fail-open does not mean workflow wrappers should perform unsafe external
mutations when CI, PR, or branch state is unknown. Wrappers fail loudly and
return bounded, resumable error state; they must never turn their own failure
into an inline hook denial.

When you touch `core/`, `detectors/`, `config.py`, or any adapter:

- Wrap risky work in `try/except` at the boundary and return an `allow` verdict
  on any unexpected error.
- Sanitize untrusted config: canonicalize modes, coerce ints with safe floors,
  refuse to escalate (`.hamingja.json` may only *relax*, never tighten).
- Keep detector mode separate from operator-budget authority: `observe` lowers
  mechanical detector blocks, but does not silently reinterpret or arm an
  operator stop.
- Upgrade runtime capability claims only from released, stable wire fields plus
  adapter fixtures. Missing, undocumented, delayed, or ambiguous observations
  may only downgrade behavior; never infer identity/lineage from timing,
  transcript layout, shared session/turn IDs, or agent prose.
- If a change *could* cause a fail-closed regression, add a test that proves
  the bad-input path still allows.

This rule takes precedence over every other guideline in this file.

## Stop / Review mode

If the user says "stop", "pause", "hold on", "why is this looping?", or "are
you making progress?", stop mutating state immediately. Only read files, inspect
`git status`/`git diff`, or list directories as needed to report:

1. Original goal.
2. Current state.
3. Files changed.
4. Diff size and any large files.
5. Hypotheses tried and ruled out.
6. Open hypotheses.
7. One minimal diagnostic next step, not a fix.

Wait for explicit approval before editing again.

## Work with the existing codebase

Before adding anything:

1. Read surrounding code and match its idioms, naming, error handling, and tests.
2. Prefer the smallest correct change; new public APIs, dependencies, or
   abstractions need real justification.
3. Do not break public behavior without flagging it: `check()`/`observe()`,
   `ToolEvent`, adapter contracts, CLI verbs, config keys.
4. Add detectors as new `detectors/` files implementing `Detector`, registered
   in `core/engine.py`. Keep harness-shaped code in adapters.

## Navigation before broad reads

Use the repo-level navigation layer before reading large files:

1. Run `hamingja code-atlas` when you need a map of symbols/sections.
2. Run `hamingja locate "<what you need>"` to get bounded line ranges.
3. Read only the suggested range, not the whole file.
4. Run `hamingja repo-health` when a large file keeps attracting broad reads
   or unrelated edits; use the output as split-pressure visibility, not an
   automatic refactor mandate.

## QA-driven development

Review, pressure-test edge cases, harden the failure paths, then build.

A "v1 limitation" is legitimate only when the unsupported case fails **safe** —
a loud error, allow-by-default, or genuinely out of scope. Unsafe defaults,
fail-closed behavior, or missed tripwires are defects; fix them or make them
loud before shipping.

Before committing a coherent slice:

1. Finish the implementation and run `python -m pytest`.
2. Re-read the diff adversarially. Ask: does every error path fail open? Can
   untrusted project config escalate or remove an exemption? Can a denied call
   wedge the session? Are read-only/idempotent tools still exempt where needed?
3. Fix every finding unless clearly out of scope.
4. Re-run the suite.

Cap the loop at 5 passes. If findings remain or a finding forks the design,
stop and ask. When a finding names a class, audit the whole class.

### Automatic staged review

Lead with findings ranked by severity, using file/line references and concrete
failure scenarios. Check fail-closed regressions, ignored generated artifacts or
state records, contradictory verdict/session records, malformed inputs, unsafe
config escalation, denied calls that wedge the session, detector exceptions,
trust-boundary drift, weak audit metadata, and missing fixtures/tests.

If a finding exposes fail-closed behavior or an unsafe missed tripwire, fix it
before commit, re-run validation, and repeat the staged review within the
5-pass cap.

After committing a coherent change, **hold for review before opening a PR**
unless the user explicitly directs otherwise.

## Tests

Every behavior change ships with tests. Use synthetic fixtures only — **never
commit captured real sessions**.

- Add a regression test that *fails before your change and passes after*. If
  you can't make it fail first, you haven't proven it tests the thing.
- Cover edges: empty input, `None`, boundary values, malformed config, throwing
  detector, missing state file. Fail-open paths are especially important.
- Run the full suite before claiming done. Capture output once so you can
  re-query without re-running:

  ```bash
  set -o pipefail
  python -m pytest -q |& tee .pytest_output.log
  # later — inspect the log instead of re-running the suite:
  grep -E "FAILED|ERROR" .pytest_output.log || true   # exit 1 on no-match is fine
  ```

  `pipefail` is required so `tee` does not mask pytest. `.pytest_output.log` is
  gitignored. Re-run only after code changes.

## Verify before claiming something is absent

Before saying "X isn't implemented" or "the library can't do this," check
`hamingja/`, `.venv/`, `__init__.py` re-exports, `config.default.json`,
adapter install scripts, README, and tests. Absence-of-use is not
absence-of-feature.

## Attack-vector debugging

Switch to systematic theory elimination when:

- 2+ fix attempts and the symptom hasn't fundamentally changed.
- You can't articulate in one sentence why the current theory would explain
  the symptom.
- You're about to write a third "let me also try X" without a model of why it
  would work.

The discipline:

1. Write the observable symptom in one sentence.
2. List every plausible theory as a numbered item, ordered by likelihood.
3. Test the top theory with the smallest measurement that would distinguish
   it from the others.
4. Eliminate or confirm. Do not change code until exactly one theory survives.
   One bug, one fix; no "while I'm here" cleanup.

For exceptions, read the **full traceback** bottom-frame-first before
enumerating a single hypothesis. The failing line is often not the one you
suspected.

## Architecture over bandaids

Prefer long-term-correct architecture over workarounds. Extract repeated
adapter/detector patterns into one reusable unit when the duplication is real;
do not speculate.

If a standard tool, validator, or test path fails and the fallback bypasses its
guarantee, fixing that failure is part of the task. No exception applies to
fail-open invariants, the config trust boundary, or adapter contracts.

## Post-merge cleanup

When the user says they merged a PR, treat that as authoritative:

1. Switch to `main`.
2. Fetch/sync `main` from origin, fast-forward-only.
3. Do not re-review, re-run validation, or inspect the old branch.
4. Delete the merged local branch if known. The user's "merged" signal is
   authorization for this specific deletion — no separate confirm needed.
5. Stop and report.

Use the hamingja wrappers when available:
- `hamingja pr-create --title <title> --body-file <path>` before `gh pr create`;
  write a concise PR body to a temporary/repo-local markdown file first, or use
  `--body -` with stdin. Do not pass a literal body string to `--body`.
- `hamingja pr-merge <pr>` before `gh pr merge`
- `hamingja post-merge-cleanup [branch]` before raw git cleanup

If a wrapper is unavailable or fails loudly, rerun the raw fallback with
`HAMINGJA_ALLOW_RAW=1` and note why.

## Communication

- Report outcomes faithfully. If tests fail, say so with the output.
- Surface uncertainty explicitly rather than presenting a guess as a finding.
- Confirm before doing anything hard to reverse or outward-facing —
  publishing, force-pushing, changing the `check()` / `observe()` /
  `ToolEvent` public surface, or modifying an installed hook config under
  `~/.claude/` or `~/.codex/`. (Branch deletion is handled by Post-merge
  cleanup; it doesn't need a second confirm there.)

## Subagent discipline

Use a subagent when reconnaissance would produce significant output in the
main context (many reads, large files). A constrained subagent returning
compact refs/verdict is cheaper overall than polluting the main thread.

- Scope the target precisely: name exact files or patterns, never "explore the codebase."
- Specify the output contract: file:line refs, one-sentence summary, no prose.
- Skip the subagent when a single `grep`/`Read` suffices, or you'd need to
  re-read its full output to proceed anyway.

## Terminology

Use primary/replica, leader/follower, coordinator/worker — not master/slave.
