### Fixed

- **The header-sequence-growth carve-out in the comparability gate accepted
  non-trailing insertions as "additive."** `_header_sequence_is_additive_reorder_free`
  previously only checked that existing headers kept their relative order to
  each other, so inserting a new header before or between existing ones (e.g.
  `[a.h, c.h]` growing to `[a.h, b.h, c.h]`) was wrongly waived as safe growth.
  Because the aggregate driver TU parses headers sequentially, an inserted
  header can change how a later header is preprocessed (macros, pragmas),
  so only strictly trailing appends (`new_list[:len(old_list)] == old_list`)
  actually preserve the old headers' preprocessing context. The gate now
  rejects mid-sequence and leading insertions instead of silently waiving
  the mismatch.
