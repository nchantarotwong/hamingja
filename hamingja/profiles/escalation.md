# escalation

Default to the day-to-day path for routine work: small edits, tests,
straightforward refactors, mechanical fixes.

Use a sub-agent when the work is bounded and isolation helps. The value is a
clean, curated context, not a longer transcript or automatically larger model.

Escalate when:
- the change touches **architecture**, **security**, **data loss** risk, **auth**, **migrations**, **public APIs**, or **language semantics**
- two distinct fixes have failed with the same symptom (your model of the bug is wrong; a fresh, isolated pass is warranted)
- the failure crosses multiple layers (e.g. frontend ↔ API ↔ DB; parser ↔ typechecker ↔ codegen)
- a non-trivial diff needs a bounded review pass
- the user explicitly asks for "deeper review", "second opinion", "think harder"

Sub-agent packet:
- problem statement
- minimal reproducer: steps/input, expected vs actual
- files/functions in scope, with paths and line ranges
- failing output, trimmed to relevant frames/assertions
- hypotheses tried and ruled out, with evidence
- open question answerable by yes/no or a specific recommendation

Do **not** dump full session history, full file contents, or the entire diff.

Model choice for the sub-agent:
- use the day-to-day model when the packet is narrow and the task is routine diagnosis or review
- use a stronger/slower model when the question is reasoning-heavy, high-risk, or two prior fixes failed with the same symptom

Division of labor:
- **Sub-agent**: diagnoses, designs, reviews, or implements the assigned bounded scope. Returns findings, changes, and validation evidence as appropriate.
- **Main agent**: implements and integrates. May delegate bounded implementation already authorized by the user when independent work benefits from isolation or parallelism. Assign explicit file ownership, scope, and validation; review the result before integration. Delegation never expands authority for publishing, destructive actions, or external communication, and must respect the user's delegation preferences and available runtime limits.

After escalation, record what you learned in the next commit message or PR
description.
