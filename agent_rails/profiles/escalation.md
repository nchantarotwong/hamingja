# escalation

Default to the fast, day-to-day model for routine work: small edits, tests,
straightforward refactors, mechanical fixes.

Escalate with a sub-agent when the work is bounded and isolation helps. The
main value is a clean, curated context, not automatically using the largest
model. A compact packet beats resending a long conversation full of dead ends.

Escalate when:
- the change touches **architecture**, **security**, **data loss** risk, **auth**, **migrations**, **public APIs**, or **language semantics**
- two distinct fixes have failed with the same symptom (your model of the bug is wrong; a fresh, isolated pass is warranted)
- the failure crosses multiple layers (e.g. frontend ↔ API ↔ DB; parser ↔ typechecker ↔ codegen)
- a non-trivial diff needs a bounded review pass
- the user explicitly asks for "deeper review", "second opinion", "think harder"

Sub-agent packet:
- one-sentence problem statement
- minimal reproducer (steps, input, expected vs actual)
- the smallest set of files / functions in scope, with paths and line ranges
- failing command/test output, trimmed to the relevant frames or assertions
- hypotheses tried and ruled out, with the evidence that ruled them out
- the open question, phrased so a yes/no or a specific recommendation answers it

Do **not** dump full session history, full file contents, or the entire diff.
The packet is curated; messy history degrades the sub-agent's output just like
it degrades yours.

Model choice for the sub-agent:
- use the day-to-day model when the packet is narrow and the task is routine diagnosis or review
- use a stronger/slower model when the question is reasoning-heavy, high-risk, or two prior fixes failed with the same symptom
- if cost or latency matters, ask before escalating model strength unless the user already requested deeper review

Division of labor:
- **Sub-agent**: diagnoses, designs, or reviews. Produces a plan or a verdict.
- **Main agent**: implements that plan. Do not delegate implementation unless the user explicitly approves it; implementation is where escalation gets expensive without proportional return.

After escalation, write down what you learned in the next commit message or PR
description, so the next person (or agent) does not pay the same cost again.
