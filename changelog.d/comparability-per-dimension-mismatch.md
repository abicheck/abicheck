### Added

- `ComparabilityMismatch` (`abicheck/comparability.py`, ADR-050's
  `--diagnostic-comparison` escape hatch) now carries a `dimensions` field
  naming which of five comparability axes — `symbol`, `declaration`,
  `layout`, `runtime`, `source` — the detected scope/profile/
  dependency-scope mismatch actually leaves unverified, computed from the
  specific extraction-contract fields that differ rather than treating
  every mismatch as equally untrustworthy. `kind`/`reason` are unchanged,
  so every existing raising and non-diagnostic caller is unaffected. This
  is the E-S2 (Block 5, `docs/contribute/plans/cli-cleanup-phase-two.md`)
  data-model slice; wiring `dimensions` into the diff pipeline's own
  per-finding assurance so a report can keep trusting an unaffected
  dimension (rather than a single report-wide `assurance: none`) is that
  same plan section's own next slice, deliberately not part of this change.
