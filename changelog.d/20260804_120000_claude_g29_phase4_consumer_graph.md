### Added

- **`compare --used-by` now explains *why* a consumer required a removed
  symbol, not just that it did.** When the old library's snapshot carries an
  L5 source graph, the application's own undefined-symbol requirements are
  folded into it as first-class graph facts (`consumer_binary` nodes,
  `CONSUMER_REQUIRES_SYMBOL`/`CONSUMER_REQUIRES_VERSION` edges) and walked
  back through `SOURCE_DECL_MAPS_TO_SYMBOL` and the call graph, so a
  `consumer_required_symbol_removed` finding names the public entry point
  behind the dependency — "`training-service` requires
  `detail::train_ops_dispatcher` via public entry `train`" — in its existing
  `impact_assessment.proof_path`/`affected_public_roots` fields. This applies
  both to the existing `func_removed`/`symbol_removed` finding for the symbol
  (in library-owned wording — "X is reachable from public entry `train`",
  since that finding is shared with the unscoped report) and, when the diff
  carried no finding for it at all, to the `consumer_required_symbol_removed`
  overlay. Works for a
  real-binary OLD as well as a saved-snapshot one — `scope_diff_to_app` takes
  the resolved old snapshot separately, for graph lookup only, so the path
  operand keeps owning every export/version read. No new
  `ChangeKind`, no report-schema change, and no verdict, finding set, or exit
  code changes. With no graph, no captured declaration, or no
  consumer-compiled public entry reaching the symbol, the finding is exactly
  what it was before — except that a symbol a public header declares is
  reported as a direct requirement without needing a walk at all. G29 Phase 4 slice 1
  ([ADR-057](docs/contribute/adr/057-consumer-graph-and-impact-join.md)).

### Changed

- **The strongest proof-path preference tier is reachable for the first
  time.** ADR-046 D6's tier 1 ("consumer-proven") had no consumer graph to
  read and was unimplemented; `select_preferred_graph_path` now prefers a
  path whose target a real `--used-by` consumer requires over a merely
  shorter one. Deliberately conservative: a path crossing a
  virtual/function-pointer call stays an over-approximation and is never
  promoted. Inert for every run without `--used-by`.
