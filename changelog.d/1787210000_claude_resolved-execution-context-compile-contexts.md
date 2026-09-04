### Changed

- **`ResolvedExecutionContext.compile_contexts` is now populated on the
  `compare` path too** (ADR-063 "One Semantic Pipeline" plan, sub-phase 4B).
  `resolve_compare_request` switched from `resolve_side_snapshot` to
  `_resolve_side_snapshot_impl` so it can recover each side's resolved
  `CompileContext` and thread it into the pair's `ResolvedExecutionContext`,
  mirroring the identical fold `execute_dump_request` already applies on the
  `dump` path. The shared decision of when recording it is safe (a
  header-AST parse actually ran, the binary format was detected, and the
  side isn't a manifest-driven dump) is now one function,
  `workflows.artifact.compile_context_gate.side_effective_compile_context`,
  used by both paths instead of the dump path's own hand-written copy.
  Behavior-preserving
  for every existing caller — `compile_contexts` was always empty before this
  on the `compare` path, and stays empty for any side this predicate excludes.
