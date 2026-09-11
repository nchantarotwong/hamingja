# Codex runtime compatibility checks

Model upgrades do not establish new hook capabilities. The Codex manifest in
`hamingja/adapters/capabilities.py` continues to promise partial pre/post tool
coverage, explicit child identity and lifecycle observations, and no parent
lineage. Upgrade those claims only with released wire documentation and
synthetic adapter fixtures for the documented fields.

The [official hook reference](https://learn.chatgpt.com/docs/hooks), checked
2026-09-10, documents unified exec as `Bash`, including completion delivered
through a later `write_stdin` poll. Polling does not repeat the original
pre-hook. It also covers `apply_patch`, MCP, and other local function tools;
`spawn_agent` has an `Agent` matcher alias. Hosted tools such as `WebSearch`
and opted-out specialized paths keep the overall capability partial.
Pre/post payloads include `tool_use_id` and `turn_id`. These fixtures supply
those fields; the current core does not use them to correlate invocations or
infer agent lineage.

The tests use invented session identifiers, commands, and outcomes. They do not
contain captured sessions or certify that a particular Codex version emits a
hook for every tool path.

| Boundary | Existing synthetic coverage |
|---|---|
| Pre-tool translation and post-tool outcomes | `tests/test_codex_adapter.py`: hook decision shape, representative success/failure results, and installer behavior |
| Concurrent result writers | `tests/test_codex_runtime_compatibility.py`: eight separate recorder processes preserve each distinct argument/outcome pair |
| Completion order | `tests/test_codex_runtime_compatibility.py`: two pre-hooks followed by reversed post-hooks retain the outcomes belonging to each payload |
| Delayed unified-exec result | `tests/test_codex_runtime_compatibility.py`: one pre-hook and a later post-hook record the original Bash command and failure |
| MCP and local results | `tests/test_codex_runtime_compatibility.py`: MCP `isError` success/failure and representative text outputs for `update_plan` and `apply_patch` |
| Interrupted call and resumed session | `tests/test_codex_runtime_compatibility.py`: a missing post-hook creates no invented result or denial; a subsequent process can record a completed call |
| Child lifecycle | `tests/test_delegation_conformance.py` and `tests/test_delegation_lifecycle.py`: explicit identity, duplicate events, and unknown-child completion handling |
| Quota evidence | `tests/test_codex_quota.py`: synthetic rollout parsing, malformed values, bounded reads, and freshness |

The interruption scenario models a missing observation, not cancellation
acknowledgement or runtime process termination. Parallel coverage exercises
append safety below the history cap; it does not reconstruct invocation order,
deduplicate tool results, or correlate opaque call IDs. History records observed
completion order. Session IDs are not evidence of parent-agent lineage.
The recorder accepts arbitrary JSON outputs and recognizes explicit error
fields in objects; opaque text is stored as an output hash and is not parsed
into a failure verdict. Local text fixtures establish transport compatibility,
not universal semantic error detection.

For a new Codex release, check its published hook contracts and tool-path
coverage before changing the adapter. Add synthetic fixtures for any changed
payload shape, including failures and missing fields. If the runtime omits an
observation or cannot establish identity, retain the documented capability gap
and fail-open behavior. Larger models and context windows alone do not remove
these limits.
