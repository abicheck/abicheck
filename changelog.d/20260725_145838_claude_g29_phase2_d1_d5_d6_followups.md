<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **ADR-046 D1's `occurrence_id` half** (G29 Phase 2 follow-up):
  `GraphEdge.occurrences` — a deduplicated, per-call-site evidence trail
  layered on top of the already-implemented `relation_key`, via the new
  `buildsource.graph_facts.edge_occurrence_id`. Strictly opt-in: it costs
  nothing and stays empty unless a producer's fact already carries
  `source_location`/`configuration_id`/`instantiation_id`/`callsite_id`
  attrs — no current producer does, so this is forward-compatible schema,
  not a change to what any pack contains today. See
  `docs/reference/source-graph-schema.md`.
- **ADR-046 D5's `effect_transitions`**: `TraversalPolicy` gained a real
  `effect_transitions` field; `CALL_GRAPH_TRAVERSAL_POLICY` now downgrades a
  reachability proof from "exact" to `"overapprox"` the moment its walk
  crosses a virtual/function-pointer call, sticky for every node reached
  transitively past it. `compute_call_graph_leak_paths` prefixes an
  over-approximated path with `"overapprox: "` so it's visibly distinct
  from a proof backed by an unbroken chain of direct calls.
- **ADR-046 D6's structured-path preference order**:
  `buildsource.graph_impact.select_preferred_graph_path` — a second proof-path
  selector (alongside `internal_leak.select_preferred_path`) operating on
  structured `list[GraphEdge]` paths rather than plain strings, so it can
  rank four of the ADR's six preference tiers (exact/high-confidence,
  public-header structural, multi-producer-confirmed, and a
  reduced-confidence residual) instead of two. Wired into
  `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`'s producer in place of a plain
  `min(..., key=len)`. `Change`/`ImpactAssessment.proof_path` gained
  `alternative_paths`/`discarded_path_count` (the ADR's
  `primary_path`/`alternative_paths[0..N]`/`discarded_path_count` finding
  shape) and `occurrence_id` (a stable, `description`-independent hash over
  a path's graph occurrences, built on the D1 addition above).
- **New reference docs**: `docs/reference/source-graph-schema.md` (the
  ADR-046 D1-D6 identity/merge/traversal-policy/proof-path-preference
  schema) and `docs/contribute/detector-impact-contract.md` (the
  required-evidence contract a future graph-derived detector must satisfy).

None of this changes which findings are produced, which are suppressed, or
any verdict — it's the remaining ADR-046 (G29 Phase 2) surface, landed
additively on top of the D2/D3 slices already shipped.
