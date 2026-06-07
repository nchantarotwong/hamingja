# base

Optimize for fast feedback, small diffs, and verified progress.

Progress means at least one of these is true:
- a failure has been reproduced
- the root cause has narrowed (a hypothesis was confirmed or ruled out)
- failing tests dropped, or new behavior gained a passing test
- the diff got smaller while staying correct
- something concrete was learned (a constraint, an invariant, a bug class)

Progress is **not**:
- more tokens spent
- more agents spawned
- a longer reasoning trace
- a wider edit
- another retry of the same approach

Default working shape:
- pick the smallest credible next step, do it, observe the result, decide again
- run the test/build loop frequently; long stretches of edits without a check are a smell
- keep diffs minimal — refactors, renames, "while I'm here" cleanup go in separate commits or get dropped
- when an attempt fails, decide before the next attempt whether you have new information; if not, stop and re-plan instead of retrying
- when a standard tool, semantic navigator, generated-artifact validator, or freshness guard fails, fix that leverage point first or make the fallback explicit to the user; do not silently replace it with a weaker grep/manual inspection path
- if you have run several read/search commands against the same target without narrowing to a bounded region, stop and state the symbol, file, or invariant you are trying to locate before searching again
- after a file/path-missing error, verify the path once with a directory listing or targeted find; do not retry the same missing target through cat, sed, head, or another spelling
- repeated batch commands need an explicit cursor, budget, and checkpoint condition; report progress against that budget before continuing another lap
- when project wrappers exist for PR merge/cleanup, CI status/failure extraction, or saved test-log summaries, use them before any manual recipe in project instructions. Manual `gh`/`git` polling and cleanup steps are fallback behavior only when the wrapper is unavailable or fails loudly

If you cannot articulate what new information the next action will produce, do not take it.
