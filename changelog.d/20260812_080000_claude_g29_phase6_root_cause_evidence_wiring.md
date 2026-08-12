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

### Fixed

- **`root_cause_evidence` now correlates across `--used-by`/`--required-symbol`
  scoped-only findings, and `root_causes[]` group-level evidence no longer
  drops for a bare-symbol pair** (review findings on the entry above). A
  regular finding whose only correlation signal was a scoped-only sibling
  (e.g. a `FUNC_REMOVED`/`CONSUMER_REQUIRED_SYMBOL_REMOVED` pair split across
  `changes` and `scoped_only_changes`) previously got no evidence at all —
  `RootCauseCorrelator` needs the real sibling `Change` object, not just its
  `caused_by_type` string, to recognize the pair as a group. Separately, a
  `root_causes[]` group's own `strongest_evidence_level`/`evidence_levels`
  were matched against the correlator's groups by `root_cause_id` equality,
  which silently missed a case the report's own grouping and the
  correlator's grouping disagree on: two correlator-eligible findings
  sharing a bare symbol with *neither* carrying `caused_by_type` (e.g.
  `--used-by --verify-runtime`'s `FUNC_REMOVED`/`CONSUMER_RUNTIME_LOAD_FAILED`
  pair) are one correlator group but two separate singleton report groups
  (`root_cause_id`'s own "only `caused_by_type` correlates findings"
  contract, unchanged) — so the two id schemes never matched even though
  each finding's own per-finding evidence already showed group membership.
  Fixed by folding each report group's already-correct member-level
  evidence directly, rather than re-deriving group membership by id. A
  third, related gap: `cli_compare_fold.py`'s own `--report-mode root-cause`
  fold-in (`_add_entries_to_root_causes`) appends a scoped-only finding's
  entry to an existing or brand-new `root_causes[]` group *after* the JSON
  serializer already built its groups, but never recomputed that group's
  own `strongest_evidence_level`/`evidence_levels` afterward — fixed by
  recomputing every touched group's evidence summary from *all* its
  findings (pre-existing and newly-folded-in alike) each time the fold-in
  runs. The evidence-folding helpers moved to a new leaf module,
  `abicheck/root_cause_evidence.py` (AI-readiness file-size cap — this
  follow-up's additions pushed `reporter.py` to 2046 lines, over the
  2000-line hard cap).
