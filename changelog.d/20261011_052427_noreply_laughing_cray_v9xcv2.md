### Changed

- **One owner for the release verdict/outcome fold** — the directory/package
  `compare` verdict ordering, the "worst verdict wins" rollup, the
  release-global (bundle/probe-matrix) verdict, `run_outcome.compatibility`,
  the proven-removed-member floor, `compare --bundle-facts`'
  `not_comparable` > `ERROR` precedence and the `--used-by` worst-consumer
  rank now all live in `abicheck/policy/release_outcome.py`
  (`fold_release_outcome`, `ReleaseOutcome`, `RELEASE_VERDICT_ORDER`). The
  private copies in `cli_compare_release_helpers`, `cli_helpers_compare`,
  `compare_bundle_facts` and `policy/reclassify` are deleted. No exit code,
  JSON field or report text changes.
