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

If you cannot articulate what new information the next action will produce, do not take it.
