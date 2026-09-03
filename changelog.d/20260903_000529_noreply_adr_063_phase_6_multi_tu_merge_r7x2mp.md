### Documentation

- **ADR-063 Phase 6's `--dump-manifest` multi-TU occurrence-detail gap is
  now documented as investigated, not merely unattempted** —
  `extract/semantic_normalizer.py`'s own docstring, the ADR, and the
  implementation plan record why the obvious fix (per-TU-fragment
  normalization before `tu_merge.merge_fragments` collapses identities)
  is a no-op with `CanonicalEntity`'s current field set: none of its four
  fields actually differ between a record's forward-declaration and
  full-definition occurrences. Genuinely closing this needs
  `CanonicalEntity` to grow a real completeness/availability
  discriminator, a model extension out of scope for a caller-ordering
  change.
