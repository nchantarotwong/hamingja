# CLAUDE.md

You are working on **agent-rails** — a small Python library that ships a
harness-neutral guardrail for LLM coding agents. It installs globally and runs
inline on every tool call, so correctness and safety dominate every other
concern. The human reviews; you do the work — so optimize for changes that are
**easy to verify and hard to get subtly wrong**.

Read `README.md` first if you haven't already; it carries the design model
(detectors, modes, trust boundary, graduated response) that the rest of this
file assumes.

## The hard rule: fail open

Every layer of agent-rails fails open. A guardrail that fails *closed* —
blocking tool calls because of its own bug, an unreadable state file, a
throwing detector, a typo in config — is worse than no guardrail at all,
because it gets installed globally and bricks every project at once.

The only thing that ever blocks a call is an **explicit, tested tripwire**.
Anything else — `except`, missing file, unparseable JSON, bad type, out-of-range
int, a detector that raises — must default to *allow*.

When you touch `core/`, `detectors/`, `config.py`, or any adapter:

- Wrap risky work in `try/except` at the boundary and return an `allow` verdict
  on any unexpected error.
- Sanitize untrusted config: canonicalize modes, coerce ints with safe floors,
  refuse to escalate (`.agent-rails.json` may only *relax*, never tighten).
- If a change *could* cause a fail-closed regression, add a test that proves
  the bad-input path still allows. This is the one class of regression that is
  unacceptable to ship.

This rule takes precedence over every other guideline in this file.

## Work with the existing codebase

This is production code with established conventions. Before adding anything:

1. Read the surrounding code and match its idioms, naming, error handling, and
   test layout. Consistency beats personal preference.
2. Prefer the smallest change that correctly solves the problem. A new public
   API, dependency, or abstraction needs a real justification.
3. Never break existing public behavior (the `check()`/`observe()` surface,
   the `ToolEvent` schema, the adapter contracts, the CLI verbs, the config
   keys) without flagging it explicitly. Adapters and hook installers are
   downstream-visible — assume someone has them wired up.
4. Adding a detector is a new file in `detectors/` implementing the `Detector`
   interface, registered in `core/engine.py`. Don't grow the engine instead.
5. Keep the detector core pure and harness-agnostic. Anything harness-shaped
   belongs in an adapter.

## QA-driven development

Review, pressure test, edge cases, harden — then build. A change isn't "done"
when the happy path works; it's done when you've actively tried to break it
and it held.

A "v1 limitation" is legitimate only when the unsupported case fails **safe** —
a loud error, allow-by-default, or genuinely out of scope. A limitation that
ships an unsafe default — silently failing **closed** (blocking a call it
shouldn't) or silently letting a tripwire condition slip through — is a defect
mislabeled, not a scope cut. Fix it, or make it loud, before shipping.

Before committing a coherent slice:

1. Finish the implementation and run `python -m pytest`.
2. Re-read the diff adversarially. Try to falsify the design, not defend it.
   The questions that matter here: does it fail open on every error path? Can
   an untrusted project config exploit it to escalate or remove an exemption?
   Does a denied call wedge the session? Are read-only / idempotent tools still
   exempt where they should be?
3. Fix every finding unless clearly out of scope.
4. Re-run the suite.

Cap the loop at 5 passes. If findings remain or a finding forks the design,
stop and ask before continuing. When a finding names a class ("this detector
mis-handles X"), audit the whole class, not just the named instance.

After committing a coherent change, **hold for review before opening a PR**
unless the user explicitly directs otherwise.

## Tests

Every behavior change ships with tests. The project uses `pytest` with
synthetic fixtures only — **never commit captured real sessions**, they can
carry private repo internals into history.

- Add a regression test that *fails before your change and passes after*. If
  you can't make it fail first, you haven't proven it tests the thing.
- Cover the edge: empty input, `None`, boundary values, malformed config, a
  detector that raises, a missing state file. The fail-open paths are the ones
  most worth testing — they're invisible when working and catastrophic when
  broken.
- Run the full suite before claiming done, not just the file you wrote.
  Capture the output once so you can re-query it without re-running:

  ```bash
  set -o pipefail
  python -m pytest -q |& tee .pytest_output.log
  # later — inspect the log instead of re-running the suite:
  grep -E "FAILED|ERROR" .pytest_output.log || true   # exit 1 on no-match is fine
  ```

  `pipefail` is required, otherwise `tee`'s exit code masks pytest's. The log
  file is gitignored. Don't re-run the suite to re-read output you already
  produced — only re-run when the code has actually changed.

## Verify before claiming something is absent

Before telling the user "X isn't implemented" or "the library can't do this,"
check every place X could live: source under `agent_rails/`, the installed
package (`.venv/`), `__init__.py` re-exports, `config.default.json`, the
adapter install scripts, README, and the tests. Absence-of-use is not
absence-of-feature.

## Attack-vector debugging

When a bug starts going in circles, switch from "fix the next obvious thing"
to systematic theory elimination.

Switch to this method when any of these are true:
- 2+ fix attempts and the symptom hasn't fundamentally changed.
- You can't articulate in one sentence why the current theory would explain
  the symptom.
- You're about to write a third "let me also try X" without a model of why it
  would work.

The discipline:

1. Write the symptom in one sentence — the actual observable behavior, not
   your guess at the cause.
2. List every plausible theory as a numbered item, ordered by likelihood.
3. Test the top theory with the smallest measurement that would distinguish
   it from the others. Usually one print or one targeted test case.
4. Eliminate or confirm. Do not write any code change until exactly one theory
   survives. One bug, one fix — no "while I'm here" cleanup stacked on top.

For exceptions, read the **full traceback** bottom-frame-first before
enumerating a single hypothesis. The failing line is often not the one you
suspected.

## Architecture over bandaids

Prefer long-term-correct architecture over short-term workarounds. When a
pattern is needed in multiple contexts (adapters, detectors), extract it into
one reusable unit rather than duplicating inline. But don't speculatively
over-engineer for needs that don't exist — three similar lines beats a
premature abstraction.

If a standard tool, validator, or test path fails and the fallback bypasses
the property that tool exists to provide, the failure is part of the current
task, not a detour around it. No exception applies to fail-open invariants,
the config trust boundary, or the adapter contracts.

## Post-merge cleanup

When the user says they merged a PR, treat that as authoritative. Do not
re-review, re-run validation, or inspect the old branch. Default action:

1. Switch to `main`.
2. Fetch/sync `main` from origin (fast-forward-only).
3. Delete the merged local branch if known. The user's "merged" signal is
   authorization for this specific deletion — no separate confirm needed.
4. Stop and report.

## Communication

- Report outcomes faithfully. If tests fail, say so with the output. Don't
  soften a bad result into a good-sounding summary.
- Surface uncertainty explicitly rather than presenting a guess as a finding.
- Confirm before doing anything hard to reverse or outward-facing —
  publishing, force-pushing, changing the `check()` / `observe()` /
  `ToolEvent` public surface, or modifying an installed hook config under
  `~/.claude/` or `~/.codex/`. (Branch deletion is handled by Post-merge
  cleanup; it doesn't need a second confirm there.)

## Terminology

Use primary/replica, leader/follower, coordinator/worker — not master/slave.
