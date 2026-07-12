# Delegation lineage investigation

Date: 2026-07-12

## Question

Can hamingja prove parent/child lineage for nested Codex and Claude Code
subagents strongly enough to enforce delegation depth or parent-scoped grants?

## Evidence

### Codex CLI 0.144.1

The current [Codex hooks reference](https://learn.chatgpt.com/docs/hooks)
documents `session_id`, `turn_id`, `agent_id`, and `agent_type` for
`SubagentStart` and `SubagentStop`. It explicitly says subagent hooks use the
parent session id. It does not define a parent-agent identifier.

The generated upstream schemas are stricter evidence: the
[`SubagentStart` input schema](https://github.com/openai/codex/blob/main/codex-rs/hooks/schema/generated/subagent-start.command.input.schema.json)
and [`SubagentStop` input schema](https://github.com/openai/codex/blob/main/codex-rs/hooks/schema/generated/subagent-stop.command.input.schema.json)
set `additionalProperties` to false and contain no `parent_agent_id` or
equivalent field.

`turn_id` is useful for grouping lifecycle events within a turn, but it does
not identify which active agent performed a nested spawn. Session and temporal
correlation therefore cannot distinguish sibling from nested children.

### Claude Code 2.1.207

The current [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
defines `session_id`, `agent_id`, and `agent_type` for `SubagentStart`.
`SubagentStop` adds the child transcript path and completion fields. Neither
event defines a parent-agent identifier. The Agent SDK hook types expose the
same boundary.

Claude's [monitoring schema](https://code.claude.com/docs/en/monitoring-usage)
does expose `parent_agent_id` on OpenTelemetry LLM/tool spans, but that is an
asynchronous monitoring channel rather than the inline hook contract. It is
also absent for agents spawned directly from the main session. Depending on a
separately configured exporter would make enforcement availability-dependent
and could lag or drop events, creating a fail-closed depth ratchet.

## Rejected correlations

- Shared `session_id`: groups a session, not a parent.
- Codex `turn_id`: groups a turn; it does not identify the spawning agent.
- Event order or active-child count: ambiguous under sibling and parallel work.
- Transcript path nesting: Codex documents transcript format as unstable;
  Claude exposes only the child's path at stop, after the depth decision.
- Claude OpenTelemetry spans: out-of-band, optional, and incomplete for direct
  children of the main session.
- Agent type or task text: neither is a stable identity relationship.

## Verdict

Parent lineage is not mechanically provable from either runtime's current
inline hook contract. Keep `delegation_lineage: false`, do not infer depth, and
retain session-concurrency advisory behavior plus the documented monotonic
fallback. Missing or undocumented lineage fields must never tighten behavior.

Revisit only when a released hook schema provides a stable parent identifier
on both start and completion events, or when a runtime supplies another
synchronous, lossless correlation contract. At that point add nested sibling,
parallel, missing-stop, malformed-parent, and out-of-order fixtures before
changing the capability manifest or enforcement response.
