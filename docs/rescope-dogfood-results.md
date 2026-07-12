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
