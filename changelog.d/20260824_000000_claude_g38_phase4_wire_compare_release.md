<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **G38 Phase 4's C-boundary signature-evidence gate is now wired into the
  real `compare --release`/bundle-analysis CLI path.**
  `find_unverified_signature_findings()` previously had no caller outside
  its own test module. `compare --release` (bundle analysis runs by
  default; `--no-bundle-analysis` opts out) now also captures each
  library's *new*-side `AbiSnapshot` alongside the already-stashed
  old-side one (`_compare_one_library`'s `collect_diff_results` gate, now
  triggered whenever bundle analysis is enabled, not only for
  `--bundle-facts-out`/JUnit), threads both maps through
  `_collect_bundle_result`/`_run_bundle_analysis` keyed by each library's
  bundle-canonical name, and folds
  `BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED` findings into the same
  `bundle_findings` list the pre-existing, already-generic
  `bundle_findings` → JSON/Markdown rendering (`BundleFinding.to_change()`,
  `render_bundle_findings_markdown()`) already renders — no reporter
  changes were needed. Accepted tradeoff: since bundle analysis is
  enabled by default, this also means both sides' `AbiSnapshot`s are now
  held in memory for the whole release, not only the old side — the same
  memory-conscious gate this module's own docstring already documents,
  now paying that cost for every default release compare rather than
  only `--bundle-facts-out`/`--format junit`.
