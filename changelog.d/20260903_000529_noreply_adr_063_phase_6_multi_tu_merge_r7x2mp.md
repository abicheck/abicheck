### Documentation

- **ADR-063 Phase 6's `--dump-manifest` multi-TU occurrence-detail gap is
  now documented as investigated, not merely unattempted** —
  `extract/semantic_normalizer.py`'s own docstring, the ADR, and the
  implementation plan record why the obvious fix (per-TU-fragment
  normalization before `tu_merge.merge_fragments` collapses identities)
  is not enough on its own: two occurrences with identical
  `CanonicalEntity` payloads are still two distinct declarations, and
  `SemanticIR.occurrences` (keyed by `OccurrenceId`) exists precisely to
  preserve that count regardless of payload equality. The real, still-open
  blocker is that nothing today distinguishes a genuine cross-TU
  declaration split from a declaration merely observed redundantly
  because many TUs `#include` the same header — both fold through the
  identical `tu_merge.merge_fragments` machinery. Genuinely closing this
  needs `tu_merge.py` to expose a new per-entity trivial-vs-genuine-variance
  signal it does not have today, not a caller-ordering change in the
  normalizer.
