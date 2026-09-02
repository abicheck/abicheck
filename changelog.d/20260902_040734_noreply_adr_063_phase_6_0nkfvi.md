### Fixed

- **An unresolved typedef no longer blocks a hybrid-merge backfill** —
  `normalize_header_ast` used to stamp both backends' `"?"`
  unresolved-underlying-type placeholder as a confirmed, present spelling;
  a resolving backend's real value then only ever became a recorded
  conflict instead of backfilling the unresolved side. It's now stamped as
  a failed fact, so a hybrid merge can pick up whichever backend actually
  resolved it.
- **A `semantic_ir_conflicts` value containing an apostrophe is renumbered
  correctly** — `renumber_conflict_keys()` passed the raw `repr()`-encoded
  value straight to its rewrite callable; text containing an apostrophe
  makes `repr()` switch to double-quoting, which the closure/anonymous
  marker rewrite deliberately treats as an opaque string literal and skips
  entirely. Values are now decoded before rewriting and re-encoded after.
