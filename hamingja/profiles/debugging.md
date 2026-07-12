# debugging

Before editing anything in response to a failure:

1. **Classify the failure.** Test failure, runtime error, build/compile error, type error, lint, performance regression, behavioral mismatch, infrastructure/env problem. Be specific — "tests fail" is not a class.
2. **Find the smallest reproducer.** A single failing test, a single command, a single input. If you cannot reproduce locally, reproducing IS the task.
3. **State a hypothesis explicitly**, in three parts:
   - **Hypothesis:** what you believe is wrong.
   - **Evidence:** the specific log line, stack frame, diff, or behavior that points there.
   - **Falsification:** the smallest observation that would prove this hypothesis wrong. If you cannot name a falsifier, the hypothesis is too vague.
4. **Diagnose before changing.** Prefer a print, a targeted test, a `git log -p` on the suspect line, or a read of the function's callers before mutating code. A diagnostic that costs one tool call is almost always cheaper than a wrong edit.

For exceptions and tracebacks: read the **full** traceback bottom-frame-first before forming a hypothesis. The failing line is often not the line you suspected.

While debugging:
- One bug, one fix. No "while I'm here" cleanup stacked on top — it muddies the signal of whether the fix worked.
- Do **not** expand the diff if the root cause is not narrowing. A growing edit on a stagnant hypothesis is the doom-loop signature.
- If two distinct fixes have failed with the same symptom, stop and re-classify the failure — your model of it is wrong.

When the fix lands, write the regression test that fails before it and passes after, unless the change already has explicit test coverage.
