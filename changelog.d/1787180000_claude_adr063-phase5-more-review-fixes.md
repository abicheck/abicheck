### Fixed

- **CastXML's `is_explicit_fact` no longer claims real evidence for an
  ordinary method.** `explicit` only applies to constructors and conversion
  functions in real C++; castxml's own `Method` tag (an ordinary member
  function) was included in the eligibility check alongside `Constructor`,
  so an ordinary method persisted a confirmed-`False` `PRESENT` fact
  instead of `NOT_APPLICABLE` — inconsistent with clang/DWARF, which
  already restrict eligibility to constructors/conversion functions.
  Confirmed empirically against a real castxml 0.7.0 run that a plain
  `Method` element never carries the `explicit` attribute at all, so this
  was a confirmed non-gap misreported as real evidence, not a case of
  losing coverage.
- **`qualified_name_segments_walk._collect_strings` now skips a
  payload-excluded `<x>_fact` sibling's whole subtree** (CodeRabbit
  review), not just its `value` — `value` happened to already be excluded
  via `_PAYLOAD_FIELD_EXCLUSIONS`'s own generic `"value"` entry (a
  coincidental name collision with `Variable.value`), but `Fact.diagnostics`
  was not: a marker-shaped diagnostic string on `source_header_fact` could
  leak into the closure-ordinal coordinate set and shift a real closure's
  assigned ordinal.
