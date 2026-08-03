### Added

- `compare --contract-evaluation` reports ADR-049's sibling contract-coverage
  ledger: `contract_coverage_failures` (one entry per provider/domain coverage
  failure for the selected `--contract` domain, each naming the provider, the
  side, the evidence record it came from, and why it failed) and
  `contract_coverage_exit_contribution` (`0` or `1`). Report schema `2.26`;
  both keys are additive and present only under that flag.

  The ledger is deliberately *not* made of findings: a coverage failure has no
  `ChangeKind` and never enters the change list, so a `--suppress` rule cannot
  reach one — ADR-049's "coverage failures are unsuppressible" holds
  structurally rather than by a rule something has to enforce. Advisory like
  the rest of the shadow evaluator: the exit contribution is stated, never
  applied.
