<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G38 bundle analysis: the severity-aware exit code now honors the same
  policy `BundleDiffResult.bundle_verdict` (the displayed verdict) already
  does.** `_fold_release_global_severity`'s bundle-findings branch called
  `compute_exit_code()` with no `policy=` at all, unlike the sibling
  `matrix_result` branch right next to it — so a built-in policy profile
  that demotes a bundle-promoted kind (e.g. `plugin_abi` demoting
  `calling_convention_changed` to `COMPATIBLE`) already read correctly in
  the displayed verdict but still forced a nonzero severity-aware exit
  code. Fixed by passing `policy=bundle_result.policy`, the same resolved
  policy name `bundle_verdict` reads. A custom `--policy-file`, a
  `kind: policy` pack override, and direct suppression of a `bundle_*`
  kind still don't reach bundle findings at all (`BundleDiffResult` has no
  fields for them yet) — tracked as a larger, separately-scoped follow-up.
