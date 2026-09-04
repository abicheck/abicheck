### Fixed

- **A late `scan` budget overflow's preserved contract-coverage/analysis-
  assurance contributions were folded into the gate but dropped from their
  own orthogonal reports** (PR review finding, immediately following the
  prior-gate-contribution fix above). `workflows/aggregate/load._load_
  report_file`'s forced-blocking branch for scan aborts folded a late
  abort's preserved `diff.exit.contract_coverage_contribution`/
  `analysis_assurance_contribution` into the target's gate `exit_code` only
  — but `AggregateResult.contract_coverage_exit`/`.analysis_assurance_exit`
  (and their own `..._targets` lists, ADR-049 Phase 7/P0.4's orthogonal
  axes) read `_LoadedReport.contract_coverage_exit`/`.analysis_assurance_
  exit` directly, which only ever read the differently-named, older
  `contract_coverage_exit_contribution`/`analysis_assurance_exit_
  contribution` fields a scan-abort payload never carries. A matrix build
  with `--contract`/`--require-complete-analysis` selected therefore
  silently lost this target from both orthogonal reports even though the
  report genuinely declared a `1` on each. Fixed via a new
  `_scan_abort_exit_axis` helper that reads each axis separately from
  `diff.exit` and folds it into the corresponding `_LoadedReport` field.
