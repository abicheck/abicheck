### Added

- **`Change.impact_assessment` — second producer caches its own assessment**
  (ADR-052 D2 follow-up, Slice 9, scoped implementation): `appcompat.py`'s
  `CONSUMER_REQUIRED_SYMBOL_REMOVED` consumer-overlay builder now calls
  `impact.engine.assess_change(overlay_change)` right after constructing the
  overlay and attaches the *returned* assessment to
  `Change.impact_assessment`, the same way `internal_leak.py`'s two builders
  did in Slice 8. Verified safe by confirming `suppression.evaluate`/
  `matches`/`would_withhold`/`would_withhold_unknown_reachability` are pure
  reads of the `Change` passed in — nothing between the cache write and a
  later `assess_change()` read touches this overlay's evidence fields.
  `source_graph_findings.py` (nine construction sites) and
  `post_processing.MarkReachability` remain unmigrated producers;
  `suppression.py`'s own named role in D2 remains unresolved (it constructs
  no `Change` of its own) — see ADR-052's "Slice 9" and "Deliberately not
  implemented this slice" sections for the per-site detail.
