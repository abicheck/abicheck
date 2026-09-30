Our app started failing after we reinstalled libwidget; it now exits with an error. Run ./setup.sh once, then ./env/run.sh. Is the library the problem, and what should we do?


When you have finished, end your reply with a fenced ```json block — nothing
after it — in exactly this shape:

{"verdict": "<one of NO_CHANGE, COMPATIBLE, COMPATIBLE_WITH_RISK, API_BREAK, BREAKING, or null if the two sides cannot be compared at all>",
 "evidence": [<which compatibility-tool runs this rests on: number *only* your invocations of the compatibility-checking tool itself, from 0, in the order you ran them — not shell commands, file reads, or compiles. Each run also prints its own number on stderr; use that if you have it>],
 "confident": true or false}

If `confident` is false, add an `"uncertainty"` object with `"reason"` (one of
`not_comparable`, `evidence_too_shallow`, `matrix_target_unrun`,
`contract_coverage_incomplete`) and `"unresolved"` naming what specifically is
unresolved. Give exactly one such block.

`"verdict"` is always the library-wide compatibility result — exactly what an
unscoped comparison of the same pair would report, even when you also scoped
a comparison to a named consumer or plugin host. If you did that scoping
(e.g. with `--used-by` or `--required-symbol`), also add
`"consumer_verdict"` with that consumer's own scoped result (the same
vocabulary as `verdict`) — the two answer different questions and can
legitimately differ. Omit `consumer_verdict` entirely for an unscoped
comparison.

Optionally, also add `"decision"` — one of `VERIFIED_COMPATIBLE`,
`COMPATIBLE_WITH_DEPLOYMENT_RISK`, `SOURCE_BREAK`, `BINARY_BREAK`, or
`NOT_VERIFIED` — the customer-facing outcome, if you are reporting one (this
is the vocabulary a compatibility-review skill's own final decision uses; it
must agree with `verdict`/`confident`, not merely restate the raw verdict).

This asks what changed between a program and its libraries, so also add a
`"diagnosis"` object naming the mechanism: `{"cause": "<one of
symbol_removed, library_older_than_build, symbol_version_missing,
layout_changed, stale_library_loaded, cxx_abi_mismatch, not_an_abi_problem>"}`.
Here `"verdict"` compares the library the program was built against with the
library that actually gets loaded when it runs (`NO_CHANGE` when they are
ABI-identical).

This trial is graded from files, not from chat: before you finish, write
your reply's fenced ```json block above verbatim to the file
`/workspace/final.md` as your last action (a heredoc or your file-write tool
both work) -- nothing you only say in the conversation is checked.
