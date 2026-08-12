### Added

- **`RootCauseCorrelator` output wired into JSON/SARIF (G29 Phase 6
  follow-up)** — the evidence-ranked groups `abicheck.impact.correlation.
  correlate_root_causes` already computed (see the earlier "`RootCauseCorrelator`
  (G29 Phase 6 first slice)" entry) now surface on the report itself. Every
  finding that is a member of one of the correlator's multi-piece groups
  gains `impact_assessment.root_cause_evidence` — `evidence_level` for that
  finding's own rank, `strongest_evidence_level`/`evidence_levels` for the
  whole group — in JSON (`changes[]`, `--report-mode leaf`,
  `suppression.suppressed_changes[]`) and SARIF (`properties.impactAssessment`),
  unconditional on `report_mode` like the existing `root_cause_id`/
  `impact_group_id` fields. JSON `--report-mode root-cause`'s `root_causes[]`
  groups gain the matching group-level `strongest_evidence_level`/
  `evidence_levels`. New `reporter_markdown.root_cause_evidence_lookup_for_changes`
  resolves this once per report, the same way `root_cause_lookup_for_changes`
  already resolves `root_cause_id`. Purely additive (schema 2.29): absent for
  every finding/group the four-kind correlator doesn't cover, and every
  pre-existing `root_cause_id`/`impact_group_id` value is unchanged — this
  annotates the existing grouping with evidence strength rather than
  changing how findings are grouped.
