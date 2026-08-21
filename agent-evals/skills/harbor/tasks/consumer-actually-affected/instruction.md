We upgraded the shared library under our analytics daemon without rebuilding it. Is that safe?


When you have finished, end your reply with a fenced ```json block — nothing
after it — in exactly this shape:

{"verdict": "<one of NO_CHANGE, COMPATIBLE, COMPATIBLE_WITH_RISK, API_BREAK, BREAKING, or null if the two sides cannot be compared at all>",
 "evidence": [<which compatibility-tool runs this rests on: number *only* your invocations of the compatibility-checking tool itself, from 0, in the order you ran them — not shell commands, file reads, or compiles. Each run also prints its own number on stderr; use that if you have it>],
 "confident": true or false}

If `confident` is false, add an `"uncertainty"` object with `"reason"` (one of
`not_comparable`, `evidence_too_shallow`, `matrix_target_unrun`,
`contract_coverage_incomplete`) and `"unresolved"` naming what specifically is
unresolved. Give exactly one such block.

If you scoped a comparison to a named consumer or plugin host (e.g. with
`--used-by` or `--required-symbol`/`--required-symbols`), also add
`"full_verdict"` with that same run's library-wide verdict (the same
vocabulary as `verdict`) — the two answer different questions and can
legitimately differ. Omit `full_verdict` entirely for an unscoped comparison.

This trial is graded from files, not from chat: before you finish, write
your reply's fenced ```json block above verbatim to the file
`/workspace/final.md` as your last action (a heredoc or your file-write tool
both work) -- nothing you only say in the conversation is checked.
