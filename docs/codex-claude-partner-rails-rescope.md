# Codex + Claude Partner Rails Rescope

Status: accepted direction — architectural consensus reached 2026-07-11
Audience: human operator, Codex, Claude
Scope: first-class support for Codex and Claude coding-agent runtimes

## Purpose

Rescope agent-rails around a narrow promise:

> Agent-rails helps Codex and Claude recover from mechanical non-convergence,
> bound accidental delegation sprawl, and complete deterministic workflows
> without trying to control ordinary reasoning or force either agent into a
> rigid behavioral lane.

The core event and detector model should remain harness-neutral. Compatibility,
testing, and operational guarantees are explicitly limited to Codex and Claude.
Other runtimes may use the generic adapter on a best-effort basis, but they are
not part of the near-term compatibility promise.

This document records the accepted architectural direction reached through
four review rounds among the operator, Codex, and Claude. Numerical constants
remain dogfood-tuning inputs; changing the resolved product or trust-boundary
decisions requires a new review.

## Product stance

Agent-rails treats the coding agent as a collaborator with judgment, not a
process to micromanage. It should:

- expose state, evidence, and consequences clearly;
- preserve operator control and straightforward overrides;
- intervene mechanically only on high-confidence signatures;
- distinguish costly work from stalled work;
- prefer recovery packets and resumable state over punishment or lockout;
- keep provider-specific behavior in adapters;
- fail open when agent-rails itself is malformed, unavailable, or uncertain.

It should not:

- block because a session is long or uses many tools;
- infer lack of progress from token or call count alone;
- prescribe one correct reasoning style;
- silently expand subagent trees;
- claim equal support for runtimes it does not test;
- make an advisory workflow profile behave like an enforcement policy.

## Deltas from current behavior

This proposal changes defaults and runtime semantics; it is not only a
restatement of principles.

| Current behavior | Proposed behavior |
|---|---|
| The enabled-by-default soft checkpoint denies after its weighted-call threshold. | The checkpoint advises by default; denial requires trusted operator opt-in. |
| The hard call ceiling is described alongside non-convergence protection. | It is labeled and configured as an operator resource budget, distinct from tripwires. Whether it remains default-on is unresolved below. |
| Post-threshold healthy-quota calls repeatedly emit “checkpoint deferred.” | Ample remaining quota suppresses the proxy checkpoint until material quota/progress state changes. |
| Subagents are counted flat per session. | Delegation uses lineage where observable and monotonic spawn grants where completion is not observable. |
| `off/observe/enforce` and response severity are partially conflated. | The three config modes remain the authority lattice; response shape becomes verdict metadata. |
| Progress credit is dominated by test/build recovery and generic clean validation. | Strong evidence binds to the active failure/workflow signature; medium structured claims require observable anchors. |

The existing config trust direction remains unchanged: packaged and operator
config are trusted; repository config may relax but not tighten.

## Support boundary

### First-class runtimes

- Codex
- Claude Code

For these runtimes, agent-rails should maintain adapter contract tests,
synthetic integration fixtures, install/upgrade coverage, interruption and
recovery tests, and documented capability gaps.

### Portable core

The following remain provider-neutral:

- normalized tool and outcome events;
- repetition, oscillation, and error-streak detection;
- semantic progress evidence;
- checkpoint and recovery state;
- delegation budgets and lineage;
- verdicts, audit events, and operator overrides;
- deterministic workflow state machines.

### Runtime adapters

Adapters own facts that genuinely differ:

- hook payload and response formats;
- which tool paths are observable or enforceable;
- success/failure event semantics;
- context and subscription-quota probes;
- subagent spawn identity and parent/child lineage;
- interruption, denial, and resume behavior;
- background-process and streaming lifecycle;
- permission/escalation representation.

The core must not parse Codex or Claude transcript formats directly.

### Unsupported runtimes

The generic API remains available, but its contract is best-effort. An
unsupported runtime must never inherit a false claim that a detector or budget
is fully enforceable. Missing observability fails open and is reported as a
capability limitation.

## Two separate systems

Agent-rails currently combines high-signal tripwires and a session call budget.
They should be treated as distinct systems with different authority.

### 1. Mechanical tripwires

Tripwires may block only when an explicit, tested signature is satisfied:

- repeated equivalent mutation after repeated failure or identical result;
- multi-lap oscillation among a bounded set of actions;
- consecutive observed errors without an intervening success;
- bypass of a configured safety/freshness leverage point;
- unauthorized destructive external action, if a runtime exposes enough
  information to identify it reliably;
- delegation beyond a trusted depth/concurrency grant.

Detector exceptions, malformed events, incomplete hook payloads, unreadable
state, and ambiguous classifications always allow.

### 2. Resource and workflow advisories

Call counts, context occupancy, subscription quota, large reads, and wrapper
recommendations are primarily advisory. They should help an agent and operator
understand cost and choose a checkpoint; they are not evidence of poisoned
judgment by themselves.

Any hard resource ceiling must be:

- explicitly enabled in trusted operator configuration;
- labeled as an operator budget, not a non-convergence detector;
- based on a legible counter whose displayed and enforced values agree;
- recoverable through one bounded approval or reset command;
- independent of observe/enforce detector mode in documentation and UI.

### Current mechanism classification

| Mechanism | System | Maximum default response |
|---|---|---|
| `repetition` | mechanical tripwire | `tripwire` when its strong evidence predicate is met |
| `oscillation` | mechanical tripwire | `tripwire` after multiple complete laps |
| `error_streak` | mechanical tripwire | `tripwire`, with block-marker recovery semantics |
| `leverage_fallback` | configured mechanical tripwire | `tripwire` only for trusted protected targets |
| `workflow_wrapper` | workflow advisory | `advise`; enforcement remains a separately reviewed narrow policy |
| `read_discipline` | workflow/resource advisory | `advise` |
| `python_command` | workflow/compatibility advisory | `advise` unless a specific tested safety predicate is documented |
| soft budget checkpoint | resource advisory | `checkpoint`, advisory by default |
| hard budget ceiling | operator resource budget | `operator_stop` only under the resolved trusted-default policy |

Moving a mechanism to a stronger maximum response requires an explicit review
and tests; sharing the detector engine does not make every mechanism a
tripwire.

## Progress model

Replace “calls since last credit” as the main checkpoint story with an explicit
progress record. A progress event is admitted only from observed evidence, not
agent narration.

Initial evidence classes:

| Evidence | Strength | Example |
|---|---:|---|
| failure reproduced | medium | one test now deterministically fails |
| hypothesis eliminated | medium | distinguishing measurement rules out cause |
| failure set shrank | strong | 12 failures become 3 |
| red to green | strong | targeted regression test passes after change |
| clean bounded validation | weak | relevant focused suite remains green |
| diff reduced without regression | medium | workaround removed, behavior retained |
| durable workflow transition | strong | PR checks complete, merge accepted |

Medium evidence such as “failure reproduced” or “hypothesis eliminated” is a
workflow/adapter-supplied structured claim, not a core inference from prose.
It requires an observable anchor: for example, a command event plus a parsed
exit transition or failure-count measurement. Credit applies only to what that
anchor proves. Missing or unparseable anchors produce no credit and no penalty.
Framework-specific failure-set parsing belongs in adapters or workflow
wrappers, not in the harness-neutral core.

Novel commands, successful reads, more prose, and growing diffs are not
progress by themselves.

Progress state should include:

- last evidence kind and timestamp/sequence;
- failure signature before and after, when available;
- bounded validation identity;
- active hypothesis identifier, when supplied by a workflow;
- weighted work since the last evidence;
- repeated-signature and oscillation state from independent detectors.

Call weights may remain as a cost estimate, but checkpoint pressure should be
computed from both work and progress. A converging session with fresh strong
evidence should not receive a checkpoint nudge on every subsequent call merely
because real quota is low.

## Delegation model

Subagents are useful for fresh-context diagnosis and bounded review. Recursive
fan-out is also one of the fastest ways to waste subscription quota.

Proposed defaults:

- maximum active children per parent: 1;
- maximum lineage depth: 1;
- child delegation disabled unless the parent grant explicitly permits it;
- reviewer/diagnostic children are read-only by default;
- every spawn requires a bounded task, output contract, and termination
  condition;
- parent retains implementation and final validation responsibility;
- completed children release concurrency but remain counted in session audit;
- the operator can raise concurrency/depth in trusted config or grant one
  additional spawn interactively.

The core should track delegation lineage rather than a flat session spawn
count. Codex and Claude adapters translate native spawn events into:

```text
DelegationEvent
  session_id
  parent_agent_id
  child_agent_id (when observable)
  depth
  active_children
  task_fingerprint
  requested_role: diagnose | review | implement | explore
  bounded_scope_present
  output_contract_present
```

If a runtime cannot provide child completion or lineage reliably, enforcement
must degrade to observe-only or a conservative monotonic explicit-spawn grant,
not guess. Active-child concurrency is enforceable only where child completion
is proven observable by adapter fixtures; otherwise an active count would only
grow and become a fail-closed ratchet.

## Poisoned-state recovery

A hard tripwire should produce a recovery artifact, not only a denial string.

Minimum recovery packet:

- detector and exact signature;
- last known progress evidence;
- repeated actions or cycle members;
- hypotheses already ruled out, if recorded;
- current diff/worktree summary reference, when available;
- one minimal diagnostic next action;
- commands for retry approval, reset, or clean-session handoff.

Recovery packet assembly itself fails open. If optional context collection or
formatting fails, the denial/recovery path still emits the detector, exact
signature, and approval/reset commands. Ruled-out hypotheses should come from
`agent-rails ledger relevant <paths>` when available rather than transcript
inference.

A block records a distinct intervention event so the session is not wedged.
Retrying the identical blocked action remains blocked; a materially different
diagnostic is allowed. Resetting detector state should not silently erase the
audit record.

For suspected context poisoning, agent-rails should support a bounded handoff
packet suitable for a fresh Codex or Claude session. Starting a new session is
a recovery technique, not a failure or punishment.

## Graduated response

The existing `off/observe/enforce` modes remain the configuration authority
axis and retain their relax-only trust ordering. The following are response
shapes carried as verdict metadata, not five new configuration modes:

1. `observe` — audit only; no injected text.
2. `advise` — concise non-blocking context with evidence and next options.
3. `checkpoint` — request a bounded plan/recovery packet; operator-configurable
   whether this can deny.
4. `tripwire` — deny one mechanically matched action; allow recovery actions.
5. `operator_stop` — reserved for explicit trusted budgets or destructive
   authority boundaries.

The system should never escalate from call volume alone to `tripwire`.

## Budget-gate rescope

Near-term changes to evaluate:

- stop emitting “checkpoint deferred” on every post-threshold call;
- rate-limit repeated advisories until state materially changes;
- display one canonical counter and threshold model;
- remove contradictory messages such as a hard limit that does not correspond
  to the visible raw or weighted count;
- distinguish proxy call pressure from real Codex quota and Claude context;
- when a real reading shows ample remaining quota (low used percentage),
  suppress proxy checkpoints entirely until material state changes;
- when a real reading shows genuine scarcity (high used percentage), escalate
  once and cite the number and window direction explicitly;
- make hard ceilings explicitly operator-budget features;
- credit reproducible narrowing and failure-count reduction, not only red to
  green and generic clean validation;
- scope progress credit to the validation/failure signature it actually proves.

## Navigation and workflow wrappers

These remain valuable but belong outside poisoned-state enforcement.

Navigation improvements:

- exclude generated/build/vendor directories from `code-atlas` by default;
- label generated artifacts instead of proposing source refactors for them;
- make `repo-health` language-aware before suggesting split names;
- provide structured output so adapters do not parse prose;
- treat truncation as an explicit incomplete result.

Workflow-wrapper improvements:

- return structured states such as `pending`, `failed`, `ready`, and `merged`;
- avoid representing expected `pending` as an undifferentiated command failure;
- keep wait/poll lifecycle inside one wrapper process contract;
- make interrupted operations safely resumable and idempotent;
- suppress sandbox-escalation nudges when escalation is already present;
- preserve the successful `pr-merge` behavior that verifies CI, confirms
  `MERGED`, and performs post-merge cleanup.

## Trust and enforcement boundary

Keep the existing fail-open invariant for agent-rails failures.

Trusted operator config may:

- enable enforcement;
- set explicit resource ceilings;
- grant delegation depth/concurrency;
- configure protected leverage points;
- define destructive-action boundaries.

Repository config may relax these controls but must not tighten a globally
installed guard against the operator's wishes.

Soft workflow profiles remain advisory markdown. They must not acquire hidden
runtime enforcement semantics.

## Conformance strategy

Maintain one shared behavioral suite and two adapter suites.

### Shared core cases

- repeated failure blocks only after the tested signature;
- repeated success and productive iteration do not block;
- oscillation requires multiple complete laps;
- malformed state/config/event fails open;
- strong progress reduces checkpoint pressure;
- unrelated green validation does not erase a relevant failure streak;
- recovery diagnostics are allowed after a block;
- identical blocked retry remains blocked;
- delegation depth and active-child limits are deterministic;
- operator override is bounded, auditable, and resumable.

### Codex adapter cases

- collaboration spawn and completion lineage;
- no recursive child spawn without a grant;
- unified/background execution lifecycle;
- interrupted wrapper recovery;
- current hook-coverage gaps remain visible;
- quota and context readings cannot independently block;
- already-escalated wrapper calls receive no redundant nudge.

### Claude adapter cases

- Task/dynamic-workflow fan-out and recursive delegation;
- PostToolUseFailure error recording;
- denied-call recovery without session wedging;
- context compaction/handoff behavior;
- quota absence falls back to advisory proxy behavior;
- approval polling and automatic resume remain bounded.

Synthetic fixtures only; never commit real captured sessions.

## Migration shape

Suggested sequence after consensus:

1. Add structured runtime capability declarations for Codex and Claude.
2. Add delegation lineage and enforce bounded spawn grants in observe mode.
3. Separate operator resource ceilings from non-convergence tripwires.
4. Add richer progress evidence, adapter-supplied anchors, and advisory deduplication.
5. Add structured recovery packets and fresh-session handoff.
6. Improve navigation and wrapper structured outputs.
7. Run both adapters in observe mode against synthetic and local dogfood flows.
8. Enable only individually reviewed tripwires; keep resource checkpoints
   advisory unless the operator explicitly opts into denial.

Each stage must preserve public `check()`/`observe()` behavior or introduce a
versioned compatibility path.

## Success criteria

- Productive long sessions do not receive repeated budget advisories.
- Identical non-convergent mutations still stop reliably.
- One bounded reviewer is easy; recursive fan-out is not accidental.
- Codex and Claude produce equivalent core verdicts for equivalent events.
- Runtime-specific gaps are explicit rather than hidden behind generic claims.
- Every hard denial has a tested signature, recovery path, and audit event.
- Operator overrides are simple, bounded, and do not require editing state.
- Generated artifacts no longer dominate navigation output or split advice.
- Workflow wrappers expose stable, structured lifecycle states.

## Consensus record

### Round-one positions

Codex and Claude agree on the following:

- default active-child target: 1, with trusted configuration able to raise it;
- depth/concurrency enforcement remains observe-only until adapter fixtures
  prove identity and completion; monotonic spawn grants are the fallback;
- soft checkpoints advise by default and deny only through trusted opt-in;
- strong progress evidence must bind to the active failure/workflow signature;
- medium evidence requires a structured claim with an observable anchor;
- response taxonomy belongs in verdict metadata, while
  `off/observe/enforce` remains the authority lattice;
- recovery packet degradation must not wedge approval or reset;
- capability declarations are static, versioned manifests that runtime probes
  may downgrade but never upgrade;
- additions use backward-compatible optional fields unless public method
  signatures or verdict ordering change;
- general destructive-action policy remains outside agent-rails enforcement;
- tuning telemetry is local aggregate counters/event kinds only, with no
  prompts, commands, paths, or captured sessions.

Historical round-one disagreement, resolved in round three:

- **Claude position:** retain a high, default-on hard call ceiling as an
  operator-budget backstop while making the soft checkpoint advisory.
- **Codex position:** a default-on call-only ceiling can still block productive
  work and therefore contradicts the product stance. This drafting session
  reached the installed hard limit during bounded, user-directed progress,
  despite healthy real quota. Prefer trusted opt-in, or make default denial
  conditional on absence of recent strong progress plus a resource-scarcity or
  mechanical non-convergence signal.

The later rounds resolved this policy with concrete unattended-runaway and
productive-long-session fixtures rather than principle alone.

### Round-two hard-ceiling response

Claude concedes that an unconditional call-count stop is indefensible and
proposes a default-on stall-gated operator stop based on volume, absence of
admitted progress, absence of recent operator turns, and absence of healthy
quota relief. Codex agrees with the first three state dimensions and with
counter-based anchors rather than wall-clock heuristics, but does not yet
accept “absence of relief” as the final positive trigger.

Two counterexamples remain:

1. The productive drafting session had one operator instruction followed by
   more than a stall window of documentation work. Documentation edits do not
   produce red-to-green, failure-set shrink, or another currently admitted
   strong/anchored-medium event. It therefore satisfies volume + stalled +
   unattended despite remaining productive and user-directed.
2. On a runtime whose manifest declares a quota probe, a probe exception or
   stale reading can remove healthy-quota relief and permit denial. That makes
   an agent-rails subsystem error contribute to a block, contrary to the
   fail-open invariant.

Proposed consensus predicate:

```text
  weighted_calls >= hard_ceiling_weighted
AND weighted_calls - weighted_at_last_progress >= stall_window_weighted
AND weighted_calls - weighted_at_last_operator >= unattended_window_weighted
AND (
      active_mechanical_nonconvergence_signal
      OR fresh_quota_used_pct >= scarcity_used_pct
    )
```

This keeps the operator stop default-on but requires positive evidence of
danger rather than treating missing evidence as danger. Repetition,
oscillation, and error-streak signals qualify only when their own tested strong
predicate is active. A fresh scarce-quota reading may qualify independently.
Call volume, lack of admitted progress, and lack of operator turns are
necessary context but are not positive non-convergence evidence by themselves.

Quota capability behavior:

- manifest declares no quota capability: the quota branch is unavailable;
  mechanical evidence may still arm the stop;
- manifest declares quota capability and provides a fresh reading: evaluate
  the scarcity branch;
- declared probe is missing, stale, malformed, or throws: the quota branch is
  false, emit a capability advisory, and never infer scarcity;
- runtime probes may downgrade a static capability claim but never upgrade it.

Acceptance fixtures must include Claude's ten proposed cases, with these
changes:

- `productive_long_session_never_stops` must include a docs/design-only phase
  longer than both windows with no test-derived progress and no recurring
  operator turn;
- `unattended_runaway_stops` must carry an active repetition, oscillation, or
  error-streak signature, not only absence of progress;
- add `novel_unattended_work_advises` for high-volume, varied work with no
  admitted progress and no positive danger signal;
- split quota absence into `runtime_without_quota_uses_mechanical_signal` and
  `declared_quota_probe_failure_disarms_quota_branch`;
- malformed budget state still allows and resets anchors as Claude proposed.

This round left one narrow historical question—whether absence of healthy
quota relief counts as danger. Round three resolved it in favor of requiring
fresh positive scarcity or mechanical evidence.

### Round-three consensus and trust-boundary refinement

Claude accepts the positive-evidence predicate and both counterexamples. The
hard ceiling is therefore default-on only as a stall-gated operator stop armed
by fresh positive mechanical non-convergence or fresh measured quota scarcity.
Unconditional call-count denial is available only as trusted operator
tightening.

Two deterministic recency rules are accepted:

- a mechanical signature is active only when its tested strong predicate was
  observed within the last `stall_window_weighted` of weighted work;
- a quota reading is fresh only within the adapter manifest's declared TTL,
  or a conservative weighted-work default when no TTL exists. Stale readings
  cannot arm the stop.

The operator stop consumes mechanical signatures as evidence independently of
the detectors' response modes. Setting repetition to `observe`, for example,
changes repetition's own response authority but does not make the historical
signature unobservable to the separately configured operator budget.

The stop's authority still follows the existing trust model:

- packaged and user/operator configuration may enable or tighten the stop;
- repository configuration may relax or disable the stop through its own
  budget-stop setting or the established repo-wide `off` mechanisms;
- `.agent-rails-off` continues to stand the entire guard down;
- relaxing a detector response mode does not implicitly disable a different
  mechanism; disabling the operator stop must target the stop or whole guard;
- evidence thresholds used by the stop come from its effective budget
  configuration, not by secretly reinterpreting a detector's relaxed mode.

This differs narrowly from Claude's original R3 statement that only trusted
configuration could disable the stop. That would revoke the documented
repo-level ability to relax or opt out and would be a trust-model change. The
refined rule preserves both properties: detector response configuration does
not accidentally disarm budget evidence, while explicit repository relaxation
continues to work as promised.

Accepted additional fixtures:

- `stale_mechanical_signal_does_not_arm`;
- `stale_quota_reading_does_not_arm`;
- `detector_observe_mode_does_not_erase_budget_evidence`;
- `repo_config_can_explicitly_relax_operator_stop`;
- `trusted_config_can_tighten_operator_stop`;
- `agent_rails_off_disarms_all_mechanisms`.

Default numerical constants remain dogfood-tuning inputs rather than product
principles: the initial proposal is a 30-weighted-call stall window, an equal
unattended window, an 85% used-quota scarcity threshold, and existing
per-task-type hard ceilings. Local aggregate telemetry and the agreed
productive/runaway fixtures must justify changing them.

### Resolved architectural questions

| # | Question | Resolution |
|---:|---|---|
| 1 | Active-child limit | 1 by default; trusted configuration may raise it. |
| 2 | Depth enforcement | Observe-only until fixtures prove identity and completion; monotonic spawn grants are the fallback. |
| 3 | Hard ceiling | Default-on only as the positive-evidence, stall-gated operator stop; unconditional denial is trusted tightening; repo relaxation remains available. |
| 4 | Progress evidence | Strong evidence binds to the active signature; medium requires a structured claim and observable anchor; weak evidence never resets stall anchors. |
| 5 | Checkpoint denial | Advisory by default; denial requires trusted opt-in. |
| 6 | Recovery minimum | Detector, exact signature, and approval/reset commands; everything else is best-effort and packet assembly fails open. |
| 7 | Capability declarations | Static versioned manifests; runtime probes may only downgrade claims. |
| 8 | API versioning | Prefer additive optional fields; use v2 only for public signature or verdict-ordering changes. |
| 9 | Destructive actions | Keep narrow existing enforcement; general destructive policy remains in router/policy systems and observe-only here. |
| 10 | Telemetry | Local aggregate counters and event-kind histograms only; never prompts, commands, paths, or captured sessions. |

### Remaining implementation decisions

These do not reopen architectural consensus:

- begin with `stall_window_weighted = 30`;
- begin with `unattended_window_weighted = 30`;
- begin with `scarcity_used_pct = 85`;
- seed `hard_ceiling_weighted` from existing per-task-type `hard_block_at`;
- use `stall_window_weighted` as the quota-freshness default when a manifest
  has no TTL;
- add an explicit repo-level budget-stop relaxation key with sanitizer tests;
- tune constants only from local aggregate telemetry and the accepted
  productive/runaway fixtures during the dogfood stage.

## Explicit non-goals

- supporting every model provider or agent harness;
- scoring model intelligence or selecting providers;
- replacing repository policy engines such as Heat Agent Router;
- evaluating semantic correctness of arbitrary code changes;
- policing style, architecture preference, or chain-of-thought;
- automatically granting recursive multi-agent workflows;
- centralizing private session telemetry.
