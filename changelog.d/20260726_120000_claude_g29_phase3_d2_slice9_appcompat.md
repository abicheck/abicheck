### Added

- **`Change.impact_assessment` — second producer constructs `ImpactAssessment`
  directly** (ADR-052 D2 follow-up, Slice 9, scoped implementation):
  `appcompat.py`'s `CONSUMER_REQUIRED_SYMBOL_REMOVED` consumer-overlay
  builder now attaches a producer-built `ImpactAssessment` to
  `Change.impact_assessment`, the same way `internal_leak.py`'s two builders
  did in Slice 8. Verified safe by confirming `suppression.evaluate`/
  `matches`/`would_withhold`/`would_withhold_unknown_reachability` are pure
  reads of the `Change` passed in — nothing between the cache write and a
  later `assess_change()` read touches this overlay's evidence fields.
  `source_graph_findings.py` (nine construction sites),
  `post_processing.MarkReachability`, and `suppression.py`'s own role in D2
  remain open follow-up work — see ADR-052's "Slice 9" and "Deliberately not
  implemented this slice" sections for the per-producer detail.
