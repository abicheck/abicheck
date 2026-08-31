### Added

- **CLI cleanup phase two, PR G2 (ADR-064 stage 1b, partial)**: `compare`/
  `scan --against` reports' `exit` block (`exit_decision.ExitDecision.to_dict`)
  now serializes all five ADR-064 fields (report schema 2.47/1.22, additive —
  always `0` for a native `compare` report). A `scan --against` run whose
  baseline is `NOT_COMPARABLE` now persists a real `diff.exit` block instead
  of a bare `{"reason": ...}`. The `compare-release` JSON summary gains an
  unconditional `exit` block reproducing `_exit_compare_release`'s own
  precedence (including a release's mode-dependent removed-required-library
  rank and its independent operational-error axis), proven — not merely
  assumed — to always agree numerically with that function's real,
  independently-tested exit code. No CLI-visible exit-code behavior changes.
