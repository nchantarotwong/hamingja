# Rescope dogfood results

Date: 2026-07-12
Mode: observe
Fixtures: synthetic only

## Adapter conformance

- Codex and Claude each received three identical failed mutations followed by
  the same candidate call.
- Both emitted a repetition `would_block` on the fourth call.
- Aggregate result: 2 sessions, 2 would-blocks, 0 enforced blocks.

## Productive long-session counterexample

- Codex and Claude each recorded 40 distinct successful commands followed by a
  new candidate command.
- Aggregate detector counts did not change: no productive-session nudge,
  would-block, or block was added.

## Tuning decision

No numerical threshold changes are justified by this run. The existing
repetition threshold caught the identical failed loop equivalently in both
adapters and stayed quiet for varied productive work. Delegation remains on
monotonic one-shot grants because fixtures prove no stable completion/lineage
signal. This file contains aggregate event counts only; no prompts, repository
paths, commands, or captured sessions are retained.

## Extended local observe-mode soak

The rescope implementation was then exercised through consecutive real feature
branch, full-suite, PR, merge, and cleanup loops. A bounded four-hour report
window avoided mixing older pre-rescope audit records into the tuning sample:

```bash
hamingja report --since-hours 4 --json
```

Aggregate result: 3 local sessions, 7 advisories, 0 would-blocks, and 0 blocks.
Detector counts were repetition 4, Python command guidance 1, read discipline
1, and workflow-wrapper guidance 1. Response shapes were 6 observe events and
1 advise event. Operator budget checkpoints remained advisory throughout; no
operator stop fired.

The wrapper advisory corresponded to a real raw-fallback attempt after a
wrapper returned without visible output, so it was actionable rather than a
false denial. The repetition advisories did not prevent any implementation,
validation, PR, merge, or cleanup stage. This small sample does not justify a
threshold change. Continue collecting bounded windows across more task shapes
before tuning defaults.

As above, these are aggregate local counters only. No prompts, commands,
repository paths, test output, or captured sessions are retained.
