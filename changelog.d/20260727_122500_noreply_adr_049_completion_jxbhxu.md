<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **ADR-049 Phase 3: `compare(..., contract_evaluation=True)` now stamps
  each finding's shadow contract-relevance decision** (`checker.py`,
  `checker_types.py`, `reporter.py`, schema 2.23): the shadow evaluator
  (`contract_evaluation.py`) existed but was never called from `compare()`.
  Passing `contract_evaluation=True` runs
  `evaluate_snapshot_pair_contract_relevance` over the final `changes` list
  -- `contract=public` when `scope_to_public_surface` is True (the default),
  `contract=all` otherwise (the exact `--no-scope-public-headers` alias) --
  and stamps three new `Change` fields: `contract_relevance`,
  `contract_reason_code`, `contract_assurance`. JSON reports serialize these
  as `contract_relevance`/`contract_reason_code`/`contract_assurance` per
  finding when present. Purely additive and non-authoritative: defaults to
  off, changes no verdict/severity/exit code, and every existing caller's
  output is byte-identical without the flag.
