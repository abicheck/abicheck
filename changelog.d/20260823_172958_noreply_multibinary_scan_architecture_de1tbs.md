<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Multibuild variant pairing (G38 Phase 3)** — new `abicheck.bundle_multibuild`
  module: `variant_fingerprint()` computes a stable identity for a release
  build variant (target triple, compiler family/version, declared feature
  toggles) without folding in build state that legitimately drifts release
  to release (macro defines, `-std=`); `pair_variants()` pairs two variant
  sets by fingerprint equality and never unions two variants' facts —
  a variant present only in the old release is reported as its own new
  `ChangeKind.BUNDLE_VARIANT_COVERAGE_REGRESSED` finding (`RISK`) via
  `coverage_regression_findings()`, and a variant present only in the new
  release is recorded as coverage expansion, never a regression finding.
  This is a backend primitive only in this change — no CLI/config surface
  yet discovers real per-variant `BundleFacts` and calls it.
