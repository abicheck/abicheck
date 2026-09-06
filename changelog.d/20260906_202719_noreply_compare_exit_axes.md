### Added

- **`compare` can now carry the evidence-contract-error (`7`) and
  budget-overflow (`5`) `ExitDecision` axes.** These previously existed only
  on `scan --against` (`docs/contribute/plans/one-comparison-product.md`
  prerequisite P3); `resolve_compare_exit_decision` now reads
  `DiffResult.evidence_contract_error`/`.budget_overflow` and, when either
  is set, folds the decision through the same ADR-064 precedence rule
  `scan` uses (`exit_decision_precedence.resolve_scan_exit_decision`),
  reused rather than re-derived. No `compare` invocation sets either field
  yet — `compare` has no `--budget` flag and no auto-strict `--depth`/
  `--source-method` enforcement (ADR-037 D5) — so every existing `compare`
  report's `exit` block is unchanged; this is prerequisite plumbing for a
  future `compare` flag to use without a second precedence rule to keep in
  sync with `scan`'s. Report schema 3.3: `evidence_contract_error_
  contribution`/`budget_overflow_contribution` are no longer `const: 0` on
  a native `compare` report, and `reasons` gains the matching
  `evidence_contract_error`/`budget_overflow` enum values.
