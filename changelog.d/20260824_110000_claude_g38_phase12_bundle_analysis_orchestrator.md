<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G38 Phase 12: live and stored-facts bundle analysis can no longer
  disagree on the Phase 4 C-boundary signature-evidence gate.**
  `bundle_facts.compare_bundle_from_facts()` (the stored-baseline
  comparison path) previously delegated only to `bundle.compare_bundle()`;
  `bundle_signature_evidence.find_unverified_signature_findings()` (Phase
  4's gate) was a separate companion only the live `compare --release` CLI
  path called directly, so a stored-facts comparison never ran it at all —
  "live vs. live" and "stored old vs. live new" bundle analysis could
  disagree on findings for identical underlying evidence.

  A new module, `bundle_analysis.py`, provides one `analyze_bundle()`
  orchestrator both paths now call: it runs the core
  `compare_bundle()` detector suite, then — when given optional per-library
  signature-evidence maps for both the old and new sides (a real
  `AbiSnapshot` or Phase 9's compact `BundleSignatureEvidence` projection,
  duck-type compatible either way) — also runs the Phase 4 gate and folds
  its findings in, with either stage's own failure recorded additively in
  `BundleDiffResult.analysis_errors` (Phase 11's structured-degradation
  contract) rather than losing the other stage's results.
  `cli_compare_release_helpers._run_bundle_analysis` (the live release
  path) and `bundle_facts.compare_bundle_from_facts()` (the stored-facts
  path) both now call `analyze_bundle()` instead of hand-sequencing the two
  stages themselves — there is only one bundle-analysis implementation left
  to drift. `compare_bundle()` remains the core graph-native/diff-derived
  detector implementation; it is no longer the complete bundle-analysis
  surface on its own.

  `compare_bundle_from_facts()` gained an optional `new_signature_evidence`
  parameter (the stored old side's own evidence is always
  `BundleFacts.per_library_snapshots`, which already is a real
  `AbiSnapshot` map) — omitted, matching every pre-existing caller exactly,
  the Phase 4 gate simply does not run. There is not yet a CLI producer
  threading a live NEW-side evidence map into a stored-facts comparison
  (G38 Phase 13, "stored-facts CLI consumer", remains a separate,
  not-yet-implemented phase) — this parameter exists for a caller (Python
  API, or a future Phase 13 CLI path) that already has one.
