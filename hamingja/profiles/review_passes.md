# review_passes

Scale review to the risk and scope of the change. A small, low-risk change
usually needs one focused review; use separate bounded passes for complex or
safety-sensitive changes. Each pass must produce specific findings or honestly say "No findings."

Choose the relevant passes below; this is not a requirement to run all five:

1. **Correctness** — does the change do what it claims for the inputs it claims to handle? Check the happy path and the stated contract.
2. **Edge cases** — empty input, `None`/null, zero, negative, very large, malformed, boundary values, concurrent access, partial failure. For each, name the edge and what the code does there.
3. **Integration impact** — callers, schema consumers, config keys. List by path.
4. **Security / regression risk** — auth, validation, injection, secrets in logs, races, removed behavior.
5. **Simplification** — duplication, removable abstraction, obsolete comments. Quality only; no new bugs hunted here.

Discipline:
- Each pass produces **specific** findings: file and line, what is wrong, what to do. "Consider improving X" without a location is not a finding.
- "No findings" is a legitimate result; do not pad with generic notes.
- Between passes, reset your framing.
- Cap the loop. If a pass has nothing new after one sweep, move on. Repeating a pass against the same diff produces diminishing returns and false positives.

Fix findings within the authorized scope in coherent changes, then revalidate
the affected behavior. A clean review is a completion signal; do not restart
the review or test cycle without new evidence or changes.
