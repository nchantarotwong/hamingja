# Changelog

All notable changes to hamingja are recorded here.

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
