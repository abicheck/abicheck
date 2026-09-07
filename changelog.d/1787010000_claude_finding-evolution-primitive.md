### Added

- **`FindingEvolution` (`introduced`/`resolved`/`persistent`/`not_evaluated`)
  on the canonical finding model** (ADR-068 Phase 1 item 2,
  `docs/contribute/plans/one-comparison-product.md`). `Change` gains an
  `evolution` field and `DiffResult` gains `resolved_findings`, plus a new
  `policy.finding_evolution` primitive (`compute_finding_evolution`/
  `compute_resolved_findings`/`apply_finding_evolution`) that classifies a
  comparison's findings against an earlier one in a chain — the reusable
  building block a longitudinal-history consumer (`workflows/history.py`,
  already landed) needs. Reports the identical `not_evaluated` state ADR-067
  D3 already established for "capability never exercised" rather than a
  second convention. A plain, single `compare()` call is unaffected: every
  finding still reports `not_evaluated` by default. The JSON report gains a
  new, unconditional top-level `finding_evolution` block
  (`report_schema_version` 3.5); Markdown/HTML rendering is deferred to a
  follow-up change. No CLI-visible behavior changes in this phase.
