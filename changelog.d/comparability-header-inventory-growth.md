### Fixed

- Comparability gate: an added public header no longer aborts the comparison.
  A `-H <dir>` public-header surface expands in sorted order, so adding one
  header (e.g. `pvxs/json.h`) lands *interior* to the declared-header sequence,
  and the `profile_fields["header_sequence"]` carve-out required a strictly
  trailing append — turning an ordinary additive release into
  `ProfileMismatchError: ... differing fields: header_sequence` with no ABI
  verdict produced. The carve-out now accepts any order-preserving insertion of
  genuinely-new, scope-corroborated headers, so a changed public-header
  inventory is compared and reported as ABI/API input. A reorder or removal of
  existing headers, and every genuine extraction-context difference
  (toolchain/ABI flags, language standard, target, include-resolution
  configuration), still fail closed — including when they ride alongside the
  waived growth.
