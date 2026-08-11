### Fixed

- **`INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API`'s `reachability_proof_path` now
  prefers an exact call-graph route over an approximate one.**
  `internal_leak._diff_call_graph_leaks` concatenates every public entry's
  own independently-discovered proof path (from
  `compute_call_graph_leak_paths`) in discovery order, not exactness order
  — so a path crossing a virtual/function-pointer dispatch from one public
  entry (`"overapprox: "`-prefixed, ADR-046 D5 `effect_transitions`) could
  precede a different entry's exact route to the same internal symbol.
  `_build_call_graph_leak_change` picked `proof_paths[0]` unconditionally,
  so the finding's stored `reachability_proof_path` — and downstream
  readers of it, including `RootCauseCorrelator`'s evidence-level ranking
  — could understate an exact-reachable symbol as merely approximate. Now
  prefers the first non-`"overapprox: "` path across the *full*
  `proof_paths` list (not just the three kept for display), falling back to
  the first path only when every candidate is approximate. This is the
  cross-entry counterpart of the exact-beats-overapprox preference
  `_consumer_compiled_reachability` already guarantees *within* one
  entry's own BFS. The finding's human-readable description now leads with
  that same selected path too (previously it kept an independent, unranked
  "first three in discovery order" slice, so it could describe nothing but
  approximate paths while `reachability_proof_path` and the correlator's
  evidence level both reported an exact proof — an internally contradictory
  finding); the total "+N more paths" count is unaffected.
