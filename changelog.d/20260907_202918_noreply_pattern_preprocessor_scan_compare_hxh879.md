### Added

- **`compare` now runs the lexical pattern pre-scan and the preprocessor
  pre-scan automatically.** `checker.compare()` runs
  `buildsource/pattern_scan.py` and `buildsource/preprocessor_scan.py`
  independently on OLD and NEW (evidence-gated per side, derived from each
  snapshot's own declared header provenance and embedded L3 build
  evidence) and folds the result into a new, always-present
  `pattern_preprocessor_scan` JSON report block, stating each escalating
  construct / macro divergence / private-header leak as `introduced`,
  `resolved`, `persistent`, or `not_evaluated` — the same evolution axis
  `cross_source_evolution` already established for the cross-source
  checks. No CLI flag exposes a way to disable it (ADR-068 D4/D5); like
  the underlying pre-scans it is advisory only and never changes a
  finding's verdict, severity, or exit code (`docs/contribute/plans/
  one-comparison-product.md` §3 #6/#8, Phase 2b).
