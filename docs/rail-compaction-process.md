# Rail Compaction Process

Use this process when reducing the size of generated `agent-rails` workflow
profiles or repo-local `CLAUDE.md` / `AGENTS.md` files. Treat the work as a
behavior-preserving refactor: the goal is fewer prompt tokens without weakening
the behaviors that keep agents from looping, mutating state after a stop, or
bypassing validation.

## Scope

This process applies to two related surfaces:

- `agent_rails/profiles/*.md`, which render into the managed
  `<!-- BEGIN agent-rails workflow profiles -->` block during
  `agent-rails init`.
- Repo-owned instruction files such as `CLAUDE.md` and `AGENTS.md`, especially
  when moving long procedures into rules, skills, or docs.

Do not hand-edit a generated block in a downstream repo as the durable fix.
Change the profile source in `agent-rails`, regenerate with `agent-rails init`,
and commit the source change.

## Non-Regression Standard

Before deleting or compressing text, classify every behavior it protects:

- **Hook-enforced:** deterministic detector or adapter behavior covers it.
  Prompt prose can usually be summarized.
- **Prompt-only high risk:** the behavior prevents state mutation, looping,
  bypassed validation, ignored user stop/pause commands, or fail-open workflow
  drift. Keep it explicit or move it into hook enforcement before compressing.
- **Prompt-only low risk:** style, preference, or explanatory detail. Compress
  or move to docs.
- **Example/detail:** keep only if it prevents a common misread. Otherwise move
  to docs or tests.

High-risk rules must remain either hook-enforced or visible in the rendered
profile text. Do not hide them only in a long external document that agents may
not load.

## Effectiveness Audit

After classifying protected behavior, judge whether the remaining wording would
actually steer a fresh-context LLM. The goal is not only fewer tokens; it is
instructions that survive skim-reading, competing context, and tool pressure.

High-effectiveness statements usually have:

- **Trigger:** when the rule applies, such as a user stop request, path-missing
  error, PR merge, stale compiler warning, or generated-artifact change.
- **Concrete action:** the command, file, report shape, or forbidden operation.
- **Failure mode:** what goes wrong if the agent ignores it.
- **Placement:** early enough that it is seen before lower-value background.
- **Low ambiguity:** mandatory wording for safety rules; "prefer" only for real
  preferences.

Low-effectiveness statements are usually:

- broad philosophy without a trigger or action
- repeated culture text that competes with sharper repo rules
- long explanations where a short hard rule would work
- safety-critical rules buried below command catalogs or optional runbooks
- "use judgment" wording where the intended behavior is deterministic

For every compacted file, include a short effectiveness pass:

1. Name the top 5-10 statements a fresh agent must retain.
2. Check that stop/review-mode behavior is early and explicit.
3. Check that QA language says to pressure-test edge cases and harden failure
   paths, not just "review."
4. Keep repo-specific sharp edges visible: generated assets, stale binaries,
   local-only privacy, audit/backup requirements, browser/device validation,
   or public API constraints.
5. Remove or compress generic motivation before removing operational rules.

If a sentence is true but unlikely to change agent behavior, compress it or move
it behind the operational rule it supports.

## Protected Invariants

Every compaction pass must preserve these behaviors:

- A user stop, pause, hold, or "why is this looping/taking so long?" request
  switches the agent into review mode.
- Review mode does not mutate files, run migrations, push, delete, or otherwise
  change state.
- The review packet reports original goal, current state, changed files, diff
  size, ruled-out hypotheses, open hypotheses, and one minimal diagnostic next
  step.
- Repeated failed attempts trigger theory elimination instead of another
  speculative edit.
- Debugging starts from a classified failure, smallest reproducer, hypothesis,
  evidence, and falsifier.
- Path-missing errors are followed by one directory/listing check before retrying
  the same target.
- Standard leverage tools, validators, freshness guards, generated-artifact
  checks, and review/audit wrappers are not silently replaced by weaker manual
  fallbacks.
- GitHub and git workflow wrappers remain discoverable and preferred: agents
  should run `agent-rails commands` before PR creation/merge/cleanup, CI
  status/failure extraction, or saved test-log summary work, and use listed
  wrappers before raw `gh`, `git`, CI polling, or manual log parsing.
- Fail-open behavior in product workflows is fixed or made loud before shipping.
- The deterministic guardrail remains fail-open on internal errors: only an
  explicit tested tripwire blocks a tool call.

If a proposed deletion weakens one of these, do not delete it until there is a
replacement in hooks, tests, or concise rendered profile text.

## Measurement Baseline

For each repo being compacted, record before/after size:

```bash
wc -l CLAUDE.md AGENTS.md 2>/dev/null
awk '{ chars += length($0) + 1; words += NF } END { print "chars", chars; print "words", words }' CLAUDE.md
rg -n '^## |^# ' CLAUDE.md
```

For generated rails, also measure the source profiles:

```bash
wc -l agent_rails/profiles/*.md
```

Use line and word count as rough indicators only. Preserve behavior over
maximal shrinkage.

## Edit Loop

1. Pick one profile or one repo-owned section.
2. List the behaviors protected by that text.
3. Classify each behavior using the non-regression standard.
4. Compact only the chosen section.
5. Add or update tests before broadening the rewrite.
6. Regenerate a sample downstream `CLAUDE.md` / `AGENTS.md`.
7. Compare rendered output against the protected invariants.
8. Run the relevant test suite.
9. Review the diff adversarially: for every removed paragraph, name the bad
   behavior it used to prevent.
10. Repeat until the rendered text is smaller and the invariant checklist still
    passes.

Do not compact multiple profiles at once unless each has its own checklist and
test coverage.

## Test Expectations

At minimum, `agent-rails` should have tests for:

- Profile rendering includes the managed block markers.
- The selected default profile set includes required invariant phrases or
  equivalent concise wording.
- Stop/non-convergence language still requires review mode and forbids
  mutation.
- Debugging language still requires reproducer, hypothesis, evidence, and
  falsifier.
- The default rendered profile still tells agents to discover workflow wrappers
  with `agent-rails commands` and prefer them over raw `gh` / `git` polling,
  PR cleanup, CI log scraping, and manual test-log parsing.
- The `workflow_wrapper` detector still nudges on raw `gh pr create`,
  `gh pr checks`, `gh pr merge`, relevant `gh run` log/status commands, and
  manual post-merge git cleanup when a wrapper exists.
- Hook-level detectors still block or nudge the same fixture transcripts as
  before.
- Internal guardrail errors still allow the tool call unless a tested tripwire
  explicitly fires.

Prefer semantic assertions over brittle full-file snapshots. Full snapshots are
acceptable only for tiny templates or smoke coverage.

## Repo Migration

When applying compacted rails to a repo:

1. Ensure the repo has a clean or understood worktree.
2. Run `agent-rails init`.
3. Verify the generated block changed only between the managed markers.
4. Keep repo-specific safety rules outside the managed markers.
5. Do not move repo-critical rules exclusively into Claude Rules or Claude-only
   Skills if Codex also works in the repo.
6. For Codex compatibility, keep `AGENTS.md` as the concise repo contract and
   use skills/docs for optional deep workflows.

If `AGENTS.md` is a symlink to `CLAUDE.md`, keep the shared file compatible
with both Claude and Codex. Put tool-specific behavior in tool-specific rule or
settings files only when the behavior is not required by the other agent.

## Done Criteria

A compaction pass is complete when:

- Rendered instruction size is measurably smaller.
- Protected invariants are still visible or hook-enforced.
- Tests cover any newly concise contract.
- A sample downstream regeneration has been inspected.
- The diff does not move safety-critical behavior into a surface that one of
  the intended agents will not read.
