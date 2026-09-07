### Added

- **Versioning policy model and deprecation-window evaluation (ADR-066
  S2).** `support_window` is declared and type-validated but not yet
  evaluated for conformance (that needs S3's real baseline resolution). A
  new `versioning:` namespace in a `--policy` YAML document
  declares five independent controls — `scheme`, `promise`,
  `support_window`, `deprecation_window`, `enforcement`
  (`abicheck.policy.versioning_policy.VersioningPolicy`), resolved through
  the existing ADR-049 D7 precedence resolver and exposed on
  `CompatibilityEvaluationConfig.versioning`. The built-in default equals
  today's behavior (strict SemVer advice, no windows, advisory only), so a
  project that never states `versioning:` sees no change.
  `abicheck.semver.recommend_release` gained an optional
  `versioning_policy` parameter: when given, the returned
  `ReleaseRecommendation.policy_acceptance` records whether the release is
  acceptable under the project's declared compatibility promise — never
  changing the observed `version_bump`/`soname_action`/`state`
  (ADR-066 D5: "policy changes acceptance; it never changes facts").
  `abicheck.workflows.history.build_longitudinal_history`/
  `run_history_request` gained an optional `versioning_policy` parameter
  that populates a new `LongitudinalHistoryResult.deprecation_compliance`
  fact — evaluating each observed removal against the policy's declared
  deprecation window, orthogonal to any pairwise `compare()` verdict.
