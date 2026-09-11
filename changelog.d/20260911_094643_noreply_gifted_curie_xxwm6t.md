<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`abicheck aggregate` no longer silently drops a `compare --no-baseline`
  audit's gate.** A no-baseline audit report's `verdict` is always `null`
  (ADR-068 D2), which `abicheck.workflows.aggregate.load._load_report_file`
  had no dedicated branch for — it fell through to the generic
  "report carried no ABI verdict" path, discarding a real, gating
  AUDIT_GATE finding for an optional or `on_missing_required: warn` target,
  and reading a completed, required audit as an unavailable coverage gap.
  A real `GateInfo` is now built from the audit's own `exit_axes`.
  `abicheck.buildsource.check_report.derive_effective_depth` also now
  reads a no-baseline audit's achieved depth from
  `run_outcome.assurance.effective_depth` (its own shape carries neither
  `old_evidence_depth`/`new_evidence_depth` nor a legacy `level` block),
  instead of reporting `check_evidence_coverage.state: "unknown"`
  unconditionally, or silently stamping the *requested* depth as achieved
  for a run that degraded.
