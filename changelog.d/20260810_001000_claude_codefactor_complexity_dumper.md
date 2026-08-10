### Changed

- **The two heaviest dumper-side functions were restructured to cut
  cyclomatic complexity** — `dumper_scoping._directly_referenced_dependency_names`
  (the dependency-header direct-reference filter) and
  `build_context._expand_response_files` (GNU `@response-file` inlining) were
  each split into named helpers, one per phase. `_expand_response_files`
  keeps its exact signature, budgets and cache semantics; the scoping filter
  additionally folds two owner maps that were built in separate passes from
  identical data into one. No behaviour, output, or public signature changes.
