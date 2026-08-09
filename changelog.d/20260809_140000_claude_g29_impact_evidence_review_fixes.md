### Fixed

- **Consumer-graph impact evidence no longer silently dropped by
  `MarkReachability`'s Slice 10 cache** (ADR-052/ADR-057 follow-up, review
  fixes): `appcompat._has_impact_evidence` treated *any* cached
  `Change.impact_assessment` as "this change already has evidence of its
  own", but `post_processing.MarkReachability` now caches an
  `ImpactAssessment` on *every* change it tags — including one left
  `UNKNOWN` with no proof path, or tagged `PROVEN_REACHABLE` via a
  direct-symbol/public-source-ABI match with no walked path either. That
  made `appcompat._enrich_covered_changes` skip the consumer-graph join for
  the ordinary, common case it exists to explain (a covered `FUNC_REMOVED`
  never got its `affected_public_roots`/`impact_proof_path`/consumer-neutral
  prose). Fixed by keying the check on
  `impact_assessment.proof_path is not None` instead. A second, related gap:
  once enrichment was allowed through, it attached the flat proof-path
  fields but left the *stale, pathless* cached `impact_assessment` object in
  place — `impact.engine.assess_change()` prefers any non-`None` cached
  assessment over re-deriving from flat fields, so a JSON/SARIF render would
  still have returned the old `proof_path=None`. Fixed by clearing
  `Change.impact_assessment` before recomputing it via `assess_change()`.
- **`impact-use-cases.yaml` manifest loading now rejects a duplicate or
  unhashable mapping key** instead of silently keeping only the last value
  (PyYAML's default) or raising a bare `TypeError` outside the documented
  `UseCaseManifestError` contract — both closed a real "declared coverage
  quietly disappears" gap in `abicheck.impact.use_cases.load_use_case_manifest`,
  and a syntactically invalid manifest document is now also wrapped in
  `UseCaseManifestError` rather than letting a bare `yaml.YAMLError` escape.
