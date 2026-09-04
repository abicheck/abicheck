### Fixed

- **A late `scan` budget overflow in audit mode (no `--baseline` at all)
  discarded the audit's own findings** (ADR-064 follow-up; PR review finding
  on the two abort-report fixes above). `run_scan_core`'s no-baseline branch
  never built a `diff_summary`, so `attach_prior_on_budget_overflow` had
  nothing to preserve when the final budget check fired after the audit
  already found an API break or an error-promoted cross-check — the persisted
  report showed a bare budget-only decision instead. `_audit_exit_code` now
  also returns a prior-decision dict
  (`abicheck.workflows.scan_abort_result.audit_prior_decision`) built from
  the same compatibility/crosscheck contributions it already computes, fed
  to the same context manager the baseline-compare path uses. Audit mode's
  own (non-aborting) report is unchanged — it still reports no diff summary
  on a normal run; only the abort path gained the preserved contributions.
- **`scan --format text --write json=...` produced no secondary JSON
  artifact on a `_BudgetOverflow`/`_EvidenceContractError` abort**, even
  though the same minimal abort report was already available for the
  primary `--format json` case. `cli_scan._emit_scan_abort_report` now
  writes to `secondary_output` whenever `secondary_fmt == "json"`, regardless
  of the primary format — the documented GitHub Action pattern (a human text
  report plus a JSON artifact for tooling) now gets that artifact on abort
  too.
