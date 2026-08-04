### Fixed

- `gate-mode: advisory` now neutralizes ADR-049's contract-coverage
  contribution alongside the compatibility gate. Zeroing only the gate left
  an advisory check still driving the trailing `aggregate` job to exit 1
  through the orthogonal coverage axis, so an advisory cell gated CI. The
  `contract_coverage_failures` ledger is untouched — advisory means not
  gating, not hiding. `deferred` reports keep their real contribution, since
  the aggregate is what computes their gate.
- `aggregate`'s text no longer names `contract.unresolved=warn` as the reason
  a target is listed but not gated. Advisory neutralization produces the same
  declared-0-with-failures shape, and the aggregate cannot tell the two
  apart; it now states the observable fact instead.
