### Fixed

- **The `header_sequence` additive-append carve-out accepted a duplicated
  appended header as safe growth.** `_header_sequence_is_additive_reorder_free`'s
  final check converted the appended tail to a `set` before comparing it
  against the newly-added scope headers — so a malformed, duplicated entry
  (e.g. `["a.h", "b.h"]` growing to `["a.h", "b.h", "c.h", "c.h"]`) was
  silently collapsed away and still authorized the waiver, even though a
  genuine `header_sequence` is always order-preserving-deduplicated by
  construction and can never contain a duplicate. `_header_sequence_is_additive_reorder_free`
  now declines whenever either the old or new list contains a duplicate
  entry, before any other check runs.
