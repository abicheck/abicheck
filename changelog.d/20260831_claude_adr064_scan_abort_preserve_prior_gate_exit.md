### Fixed

- **A late `scan` budget overflow's aggregate gate downgraded a real
  already-found break to a bare coverage-incomplete exit** (PR review
  finding). A *late* `_BudgetOverflow` (one that fires after a baseline
  comparison or audit already computed a real gate/coverage/assurance/
  crosscheck decision) preserves that prior decision's `compatibility_
  contribution`/`contract_coverage_contribution`/`analysis_assurance_
  contribution`/`crosscheck_promotion_contribution` in the report's
  `diff.exit` block (`attach_prior_on_budget_overflow`, landed earlier this
  session) — but `workflows/aggregate/load._load_report_file`'s
  forced-blocking branch for scan aborts never read them, unconditionally
  setting the target's gate `exit_code` to `COVERAGE_INCOMPLETE_EXIT` (1)
  even when a real ABI break (`compatibility_contribution: 4`) had already
  been found before the abort fired. A downstream severity-aware consumer
  therefore saw the aggregate exit as a mere coverage gap rather than the
  real break it actually was. Fixed via a new `_scan_abort_prior_exit`
  helper that reads the largest valid preserved contribution from
  `diff.exit` and folds it with `max()` against the coverage floor — an
  early abort (no prior decision, every contribution `0`) still floors at
  `1` exactly as before; a malformed/out-of-scheme contribution is ignored
  rather than trusted.
