### Changed

- **`ResolvedExecutionContext` gets its first real consumer** (ADR-063 "One
  Semantic Pipeline" plan, sub-phase 4B). `service_compare_pipeline.
  classify_compare_pair` — the shared classification half the typed Python
  API's `run_compare_request` runs through (the native `compare` CLI calls
  `compare_snapshots()` directly and does not go through this function) —
  now stamps `DiffResult.requested_depth` by reading `pair.
  resolved_execution_context.requested_depth`, when it agrees with the
  call's own `request.depth`, instead of independently re-normalizing
  `request.depth` a second time. The two values were always identical in
  every real invocation, so no existing behavior changes; a caller that
  hand-constructs a `ResolvedComparePair` with no context attached, or
  passes a `request` whose depth disagrees with the one that resolved the
  pair, still falls back to the direct `request.depth` computation.
