<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Unified `impact_assessment` object and always-present `reachability_state`
  field in JSON/SARIF reports** (ADR-050, G29 Phase 3 slice 1): every finding
  now carries `reachability_state` (`reachable`/`unreachable`/`unknown`) — the
  tri-state reachability signal `Change.reachability_state` has carried in
  memory since PR #607, but which was never serialized until now, so a
  `PROVEN_UNREACHABLE` finding and one the graph walk never examined at all
  were previously indistinguishable in output. Findings with reachability or
  graph-impact evidence additionally gain `impact_assessment`, a single
  object bundling that evidence (`reachability_state`, `public_reachable`,
  `reachability_kind`, the proof path, suppression decision state, and
  `evidence_category`/`correlated_change_kind`) instead of several
  separately-named keys. New `abicheck/impact/` package
  (`ImpactAssessment`/`GraphProofPath`/`FindingDecision`/`assess_change`).
  `report_schema_version` 2.12 → 2.13; every existing field is unchanged.
  See `docs/concepts/impact-analysis.md`.
