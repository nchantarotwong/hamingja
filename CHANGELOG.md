# Changelog

All notable changes to hamingja are recorded here.

## 0.1.1 - 2026-09-10

- Require a fresh, timezone-aware Codex quota event timestamp. Unrelated
  rollout activity can no longer revive stale scarcity evidence for an
  operator stop; missing or invalid timestamps fall back to the configured
  budget behavior (advisory by default).
- Reject non-finite/out-of-range quota percentages and wildcard session IDs;
  expire rolling and weekly quota evidence independently at their reset times.
- Validate pull requests and release artifacts with the synthetic suite,
  distribution metadata checks, and an isolated installed-wheel smoke test.
- Keep ordinary status questions from forcing a stop, allow bounded
  implementation delegation within existing authorization, and scale review
  and verification to risk without encouraging tests for budget credit.
- Add synthetic concurrent, delayed, and interrupted Codex hook fixtures;
  refresh documented tool coverage without claiming full runtime coverage.
- Add an observe-mode evaluation protocol and aggregate baseline. Detector
  thresholds and public event/adapter contracts remain unchanged.

Upgrade with `pipx upgrade hamingja`, refresh hooks with `hamingja install all`,
and restart running sessions. Preview updated workflow instructions with
`hamingja init --dry-run`, then use `hamingja init` to refresh the managed block;
unmanaged instructions remain operator-owned. Avoid `--force` for upgrades.

## 0.1.0 - 2026-07-12

- Launch under the Hamingja name. Installers recognize pre-release
  `agent_rails/adapters/` hook paths once so editable-checkout users can refresh
  them in place with `hamingja install all`.
- Establish Python 3.13 as the supported runtime floor.
- Add fail-open mechanical tripwires and observe/enforce rollout controls.
- Add progress-aware operator budgets, quota/context signals, operator-turn
  recency, bounded approvals, and recovery handoffs.
- Add first-class Claude Code and Codex adapters with versioned capability
  declarations, child lifecycle observability, and prompt-free operator anchors.
- Add framework failure-set progress extraction for pytest, unittest, Cargo,
  and Jest-family runs.
- Add deterministic navigation, ledger, PR, CI, cleanup, and test-summary
  workflows with structured resumable states.
- Add preserving, idempotent hook installation and uninstall for both runtimes.
