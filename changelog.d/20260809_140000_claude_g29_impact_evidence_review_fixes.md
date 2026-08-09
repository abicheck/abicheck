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
  A bare `ValueError` from PyYAML's implicit-timestamp scalar constructor
  (e.g. `use_case: 2023-99-99`) is now wrapped the same way.
- **`appcompat._merge_consumer_impact_paths`'s same-root alternative-path
  filter now also applies to the single-match case** (review fixes):
  `impact.consumer_graph.explain_required_symbols` can itself return one
  `ConsumerImpactPath` whose own `alternative_entry_paths` already mixes
  candidates from more than one consumer-compiled entry — not just a
  multi-symbol merge — so the previous `len(matches) == 1: return
  matches[0]` early return skipped the root filter for the *ordinary*
  single-symbol case, letting a differently-rooted alternative be
  serialized in JSON/SARIF under the primary's own root
  (`impact.engine._build_proof_path`'s single `affected_public_roots[0]`).
- **`impact.use_cases.join_use_case_graph` no longer leaves a stale,
  inherited `graph_id` on its returned graph** (Codex review, fresh
  evidence): unlike `impact.consumer_graph.join_consumer_graph` — whose
  result never leaves `appcompat.py`'s own in-memory analysis —
  `join_use_case_graph` is this module's documented public Python API, so a
  caller following `docs/use/use-case-impact.md` and calling
  `joined.to_dict()` is a real, reachable path. Left un-cleared, the deep
  copy inherited `library_graph`'s own already-finalized `graph_id` even
  though the joined node/edge content had changed, and `to_dict()` only
  recomputes an id when the stored value is empty — silently describing
  the join's different content under an unrelated, stale id. Fixed by
  clearing `joined.graph_id` before returning.
