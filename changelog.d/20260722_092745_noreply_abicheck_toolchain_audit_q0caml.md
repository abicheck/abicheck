### Fixed

- **The C++20 dialect detector no longer misreads text inside a
  backslash-continued string literal as real code.** `_iter_logical_lines`
  splices away a backslash-newline continuation, embedding a literal
  newline in its place — but the plain string-literal pattern deliberately
  refuses to match across a newline, so a continued string like
  `"requires \` + newline + `{ ... }"` was left completely unstripped, and
  its "requires"/"{" on either side of the embedded newline reached the
  requires-expression pattern as if they were real code. A newline-tolerant
  variant is now used at the two per-logical-line scan sites, where an
  embedded newline can only come from a genuine continuation (safe to cross)
  rather than an unrelated later line (unsafe to cross).
