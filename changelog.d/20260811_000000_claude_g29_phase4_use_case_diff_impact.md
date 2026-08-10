<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`abicheck project validate-use-cases --against-new`** (G29 Phase 4,
  ADR-057 amendment): the `impact-use-cases.yaml` manifest can now be
  folded into a real two-snapshot diff. Given `--against <old-snapshot>
  --against-new <new-snapshot>`, the command diffs the two (via the same
  Tier-2 `service.compare_snapshots` every other front end routes through)
  and reports, per declared use case, which of the resulting changes its
  own resolved entrypoints can be shown to reach — a change whose symbol
  no use case's entrypoints reach is reported as unattributed, never
  silently dropped. New library function `impact.use_cases.
  explain_use_case_impact()` powers this: the use-case counterpart of
  `impact.consumer_graph.explain_required_symbols()`, reusing the
  identical restricted call-graph walk (`CALL_GRAPH_TRAVERSAL_POLICY`)
  but rooted at a manifest's own declared entrypoints instead of every
  public entry in the library. This is a read-only CLI report — it sets
  no field on any `Change` object, adds no `ChangeKind`, and changes no
  `compare` exit code or report schema; a genuine `compare`-native
  `affected_use_cases` field and `USE_CASE_IMPACT_CONFIRMED` finding
  remain G29 Phase 6 scope.
