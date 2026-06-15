## File Reading Discipline

Unscoped reads of large files are the primary source of excess token usage in a
coding session.  File content stays in the context window for every subsequent
turn and compounds with each new message.

### Rule: grep before Read on any file you haven't opened yet

1. Run `grep -n "target_symbol\|SectionHeader" path/to/file` to locate the
   relevant line numbers.
2. `Read` with `offset` and `limit` targeting only that window.

Full unscoped `Read` (no `offset`, no `limit`) is appropriate only for files
under ~150 lines.  For anything larger, an unscoped read is avoidable waste.

### When you need multiple sections

Read each section separately with its own bounded window.  Do not read the
whole file to avoid a second grep.

### agent-rails enforcement

The `read_discipline` detector tracks unscoped reads of large files per
session:

- **2nd unscoped read of the same file** → nudge: next read will be blocked.
- **3rd unscoped read of the same file** → blocked: use grep + offset/limit.
- **1st unscoped read of a 1000+ line file** → blocked: locate the section
  first, then read a bounded window.

The tripwire hook emits an advisory before every unscoped read of a file over
200 lines, regardless of repetition count.
