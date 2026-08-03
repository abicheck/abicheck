<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **The contract-coverage exit is now applied, not just reported (ADR-049
  Phase 7).** `compare` and `scan --against` have emitted
  `contract_coverage_exit_contribution` since the coverage ledger landed, and
  it was deliberately inert — the point was to let a run show what the flip
  would do before it did it. It now moves the process exit status. Under
  `--contract-evaluation`, a selected contract domain that cannot close on the
  evidence available (for example `--contract exports` against a snapshot pair
  carrying no export table) contributes exit **1**.

  The axis is orthogonal, exactly as ADR-049 §7 specifies: it is folded with
  `max`, so a coverage failure raises a clean `0` to `1` and can never lower a
  gate's `2`/`4` — a real ABI break is never demoted to "warnings only" by
  missing coverage. It equally never rewrites a finding's compatibility
  decision or its gate contribution; it is a floor on the exit status and
  nothing else. `compare` and `scan --against` fold it identically (§6.4's
  cross-command parity Gate).

  **A run that does not pass `--contract-evaluation` is unaffected**: with no
  selected domain there is nothing to be short of evidence for, so every
  pre-existing invocation exits exactly as before.

  To accept incomplete contract assurance, set `contract.unresolved: warn`
  (ADR-049 D9) — the documented mechanism for precisely this. It zeroes the
  coverage contribution and changes nothing else: the failures stay in
  `contract_coverage_failures`, stay unsuppressible, and stay in every report.
  Accepting incomplete assurance is not the same as hiding it. The reported
  `contract_coverage_exit_contribution` is now the *applied* number, derived by
  the same function the exit path uses, so the ledger a user reads is the one
  that gated them.

### Added

- **`contract.unresolved` is now an applied `kind: contract` pack field.** It
  was rejected as "resolvable but not applied by this build" until its engine
  consumer existed; the coverage exit above is that consumer, so a contract
  pack may now assign it. It is the one applied pack field that is not folded
  into a legacy object — its consumer reads it straight off the resolved
  configuration the receipt already persists.
