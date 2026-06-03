# review_passes

Review with several bounded passes, not one giant pass. One giant pass burns
context, mixes concerns, and tends to produce generic praise ("looks good
overall, consider adding more tests"). Bounded passes produce specific
findings.

Suggested passes, run as separate focused reviews:

1. **Correctness** — does the change do what it claims for the inputs it claims to handle? Check the happy path and the stated contract.
2. **Edge cases** — empty input, `None`/null, zero, negative, very large, malformed, boundary values, concurrent access, partial failure. For each, name the edge and what the code does there.
3. **Integration impact** — every caller of every changed function, every consumer of every changed schema, every config key that moved. List them by path.
4. **Security / regression risk** — auth, input validation, injection, secrets in logs, race conditions, things the change *removes* that something else depended on.
5. **Simplification** — duplication that can collapse, an abstraction that can come out, a comment that can go because the code now says it. Quality only, no new bugs hunted here.

Discipline:
- Each pass produces **specific** findings: file and line, what is wrong, what to do. "Consider improving X" without a location is not a finding.
- "No findings" is a legitimate result for a pass. Prefer one honest empty pass to padding it with generic notes.
- Between passes, reset your working set — you are looking for a different class of thing each time, and carrying the previous pass's framing dulls the next one.
- Cap the loop. If a pass has nothing new after one sweep, move on. Repeating a pass against the same diff produces diminishing returns and false positives.

When findings come in, fix them in small commits aligned to the pass that found them — do not bundle "edge case fix" with "simplification" in one commit.
