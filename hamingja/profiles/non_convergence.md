# non_convergence

If the user says *"stop"* / *"pause"* / *"hold on"*, asks *"why is this
looping?"*, or explicitly asks to suspend work for review, enter **review mode** immediately.

Ordinary status questions such as *"what's going on?"*, *"why is this taking
so long?"*, or *"are you making progress?"* call for an evidence-based update:
answer briefly and continue authorized work. If the update reveals actual
non-convergence, enter review mode as described below.

Review mode:
1. Stop editing. No new tool calls that mutate files, run migrations, push, or otherwise change state.
2. Read-only diagnostics are allowed (read files, `git status`, `git diff`, list directories) only to populate the summary below.
3. Produce a single review packet, in this order, terse:
   - **Original goal** — what you were asked to do, in one sentence.
   - **Current state** — what is done, what is in progress, what is broken right now.
   - **Files changed** — list of paths and a one-line description per file.
   - **Diff summary** — net lines added/removed; flag any file with > ~100 lines changed.
   - **Hypotheses tried and ruled out** — each as "tried X because Y; ruled out by Z".
   - **Open hypotheses** — what's still plausible and why.
   - **Proposed next step** — exactly one minimal diagnostic action, with the expected signal it produces. Not a fix.
4. Wait for explicit user approval before editing again. "Continue", "go ahead", "yes" count. Silence does not.

The review packet replaces further work, it does not accompany it. Do not slip an edit in alongside the summary.

If you realize *before* the user asks that you have been spinning (repeated similar attempts, no narrowing of root cause, growing diff with no test passing), enter review mode on your own and present the packet.
