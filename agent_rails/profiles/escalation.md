# escalation

Default to the fast, day-to-day model for routine work: small edits, tests,
straightforward refactors, mechanical fixes.

Escalate to a stronger/slower model **only** when the work is bounded and the
extra reasoning will land somewhere specific. Wandering with a stronger model
is still wandering, and it costs more.

Escalate when:
- the change touches **architecture**, **security**, **data loss** risk, **auth**, **migrations**, **public APIs**, or **language semantics**
- two distinct fixes have failed with the same symptom (your model of the bug is wrong; a fresh, deeper pass is warranted)
- the failure crosses multiple layers (e.g. frontend ↔ API ↔ DB; parser ↔ typechecker ↔ codegen)
- the user explicitly asks for "deeper review", "second opinion", "think harder"

Escalation packet — what you send to the stronger model:
- one-sentence problem statement
- minimal reproducer (steps, input, expected vs actual)
- the smallest set of files / functions in scope, with paths and line ranges
- hypotheses tried and ruled out, with the evidence that ruled them out
- the open question, phrased so a yes/no or a specific recommendation answers it

Do **not** dump full session history, full file contents, or the entire diff. The packet is curated; messy history degrades the stronger model's output just like it degrades yours.

Division of labor:
- **Stronger model**: diagnoses, designs, or reviews. Produces a plan or a verdict.
- **Default model**: implements that plan. Do not escalate the implementation unless the user explicitly approves it — that is where escalation gets expensive without proportional return.

After escalation, write down what you learned in the next commit message or PR description, so the next person (or agent) does not pay the same cost again.
