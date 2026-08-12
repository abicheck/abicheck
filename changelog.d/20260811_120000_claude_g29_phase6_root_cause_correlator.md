### Added

- **`RootCauseCorrelator` (G29 Phase 6 first slice)** —
  `abicheck/impact/correlation.py`'s `correlate_root_causes` groups the four
  independent findings that each answer "does a load against the new
  library fail because of *this* symbol" — `FUNC_REMOVED` (artifact-level:
  the symbol vanished from the export table), `INTERNAL_SYMBOL_REQUIRED_BY_
  PUBLIC_API` (call-graph-level, from `internal_leak.py`),
  `CONSUMER_REQUIRED_SYMBOL_REMOVED` (consumer-level, from `appcompat.py`),
  and `CONSUMER_RUNTIME_LOAD_FAILED` (runtime-level, from
  `cli_helpers_compare.py`) — into one `RootCauseGroup` per underlying
  symbol, with each member tagged with its own evidence level
  (`artifact_proven` → `call_graph_overapprox` → `call_graph_proven` →
  `consumer_proven` → `runtime_proven`, weakest to strongest). The level is
  not purely a function of `ChangeKind`: it's promoted to `consumer_proven`
  when `Change.reachability_kind` says so (covering
  `appcompat._attach_consumer_impact`, which enriches an existing
  `FUNC_REMOVED` in place instead of emitting a separate overlay when the
  symbol is already covered), and downgraded to `call_graph_overapprox` when
  an `INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API` finding's own proof path
  crossed a virtual/function-pointer dispatch
  (`internal_leak.compute_call_graph_leak_paths`'s `"overapprox: "` prefix)
  and therefore proves only a *possible* dispatch target, not an exact
  chain. A composer, not a detector: it introduces no new `ChangeKind`, no
  report schema change, and no verdict/exit-code effect — it only groups
  and labels findings the pipeline already produces. A symbol with just one
  correlated piece isn't returned as a group, mirroring
  `reporter_markdown.root_cause_for_change`'s existing singleton-exclusion
  convention. This is Phase 6's first item (the plan's `RootCauseCorrelator`
  composer); the JSON/SARIF `root_cause_id` surface and the eight new
  detector/overlay kinds remain open, each its own follow-up.
