### Added

- **An optional `impact-use-cases.yaml` manifest declares a project's own
  business/runtime use cases and joins them onto the library's own graph.**
  `abicheck.impact.use_cases` parses a top-level YAML list of
  `use_case`/`entrypoints`/`tests` entries into `UseCaseDefinition`s,
  promotes each to a `use_case` graph node (and each `tests` entry to a
  `test_case` node with a `TEST_COVERS_USE_CASE` edge onto it), and resolves
  each `entrypoints` name against the library graph's own exported
  `binary_symbol`/public `source_decl` nodes to emit a `USE_CASE_USES_ENTRY`
  edge — matched by either the node's own id or its label. An entrypoint
  the library graph cannot resolve is silently skipped, never an error;
  only a structurally malformed manifest document (not a YAML list, a
  non-mapping entry, a missing/blank `use_case` name) raises the new
  `UseCaseManifestError`. `join_use_case_graph` folds the declared facts
  into a **deep copy** of the library graph, mirroring
  `consumer_graph.join_consumer_graph`'s identical mutation-safety
  discipline. Deliberately a separate schema from
  `docs/contribute/usecase-registry.yaml` (abicheck's own feature-coverage
  registry — an unrelated concept). G29 Phase 4 slice 2, amending
  [ADR-057](docs/contribute/adr/057-consumer-graph-and-impact-join.md).
  Runtime-trace ingestion (`TRACE_OBSERVED_ENTRY`/`TRACE_OBSERVED_EDGE`) and
  any report-level `affected_use_cases`/`USE_CASE_IMPACT_CONFIRMED` surface
  remain out of scope for this slice — see
  [Use-Case Impact](docs/use/use-case-impact.md).
