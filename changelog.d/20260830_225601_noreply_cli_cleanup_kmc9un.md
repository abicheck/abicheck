### Added

- **CLI cleanup phase two, PR G2 (ADR-064)**: `exit_decision.py` gained
  `resolve_scan_exit_decision` and `resolve_release_exit_decision`, pure
  resolvers reproducing `scan`'s evidence-contract-error/budget-overflow/
  not-comparable precedence and a directory/package release's
  mode-dependent removed-required-library rank plus an independent,
  tie-foldable operational-error axis (a library's dump/extract/compare
  failure, distinct from a real compatibility-gate finding even when both
  happen to tie). Neither resolver is wired into any command's
  actually-returned exit code yet -- no CLI behavior changes.
  [ADR-064](docs/contribute/adr/064-canonical-gate-algorithm-and-exit-decision.md)
  records the full design (including the eventual `--exit-code-scheme`
  removal) this additive step works toward.
