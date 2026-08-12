### Fixed

- **`--show-only`'s dangling-correlation fix now also clears a cached
  `impact_assessment`'s `correlated_change_kind`** — clearing only the
  top-level field on a filtered finding left its shared (shallow-copied)
  `impact_assessment` object still carrying the stale reference when a
  reachability-aware suppression had caused it to be cached, so a
  filtered report's nested `impact_assessment.correlated_change_kind`
  could still name a finding the same filter excluded. The original
  `Change`'s cached assessment is never mutated.

