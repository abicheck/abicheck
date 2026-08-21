### Changed

- **Internal: `GateConfig` (ADR-049's `CompatibilityEvaluationConfig`) can
  now express `require_complete_analysis` and an ADR-043 scoped-gate
  selection (`--used-by`/`--required-symbol`) via a new
  `ScopedGateSelection` type.** Both fields default to "no effect," so no
  existing behavior changes — nothing constructs or reads them yet. First
  step of extending the existing D7 config object into the
  duplication-and-convergence plan's Phase 2 runtime contract, rather than
  introducing a duplicate sibling type.
