### Changed

- **`ResolvedExecutionContext` gets its first real consumer** (ADR-063 "One
  Semantic Pipeline" plan, sub-phase 4B). `service_compare_pipeline.
  classify_compare_pair` — the shared classification half every front end
  (`compare`'s native CLI, the typed Python API, MCP) resolves through — now
  stamps `DiffResult.requested_depth` by reading `pair.
  resolved_execution_context.requested_depth` (built once, from the same
  `AnalysisPlan` `resolve_compare_request` already resolves for its ADR-063
  Phase 4 pre-flight check) instead of independently re-normalizing
  `request.depth` a second time. The two values were always identical in
  every real invocation, so no existing behavior changes; a caller that
  hand-constructs a `ResolvedComparePair` with no context attached (as some
  unit tests do) still falls back to the direct computation.
