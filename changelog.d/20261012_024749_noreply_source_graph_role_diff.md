### Fixed

- **The source-graph diff no longer misses a changed dependency role** — when a
  public declaration kept its dependency on a type but used it differently
  (`param` → `return`, or a second role added), the structural edge diff read
  as no change. `diff_source_graph` now matches edges on their full
  `relation_key()` and reports such a dependency in the new
  `GraphSummaryDiff.changed_edges` (ADR-077 D4), and a public entry's role
  change is reported as `public_dependency_role_changed` (RISK). A graph
  stored before edge roles were recorded is not read as a change: the
  dependency goes to `role_unknown_edges` and the report carries a
  `public_dependency_role_unknown` coverage note (COMPATIBLE). The coarse
  `added_edges`/`removed_edges` are unchanged. Two names moved to their
  model owners: `GraphSummaryDiff` (now `abicheck.model.source_graph_diff`;
  `abicheck.buildsource.GraphSummaryDiff` still resolves) and
  `ROLE_COVERAGE_MATRIX` (now `abicheck.model.source_graph_coverage`).
