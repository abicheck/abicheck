### Fixed

- The published `compare_report` schema's `contract_coverage_failures` entry
  is now pinned to `CoverageFailure.to_dict()` by a test, not just by having
  been edited to match once. Nothing derives one from the other, so a field
  added to either side alone would otherwise make every published report
  fail a consumer's validation.

### Changed

- `service_scan._scan_request_config` inlines its resolver call instead of
  routing it through a helper that took a function and an enum class as
  untyped parameters purely to keep one `try` statement short.
- Both scan front ends now document why their resolver-exception nets differ
  (the API accepts a free-string `policy`; the CLI's is a `click.Choice`), so
  the asymmetry reads as deliberate rather than as drift.
- `REQUIRED_PROVIDERS` states why the overlay providers are absent from every
  domain: an overlay is additive to a domain rather than constitutive of it,
  so a failed overlay is not a reason the domain could not close.
- `suppression_reaches_coverage_failures` no longer overclaims. It witnesses
  one specific mutation — giving `CoverageFailure` the attributes the matcher
  keys on — and says so, rather than implying it proves the general
  unsuppressibility guarantee.

### Documentation

- Corrected a false claim that the MCP `abi_compare` tool takes no scope
  parameter. It takes two — `used_by` and `required_symbols` — and they
  rewrite the verdict and exit code. No field of
  `CompatibilityEvaluationConfig` models a consumer scope, so a scoped run's
  resolved config is indistinguishable from an unscoped one's; that is now
  recorded as a known gap in the reference, the module, and a test, rather
  than described as an accurate "nothing was chosen".

### Fixed (second round)

- Two inputs the comparison accepts no longer fail during receipt
  resolution, on `scan --against`/`run_scan` and the MCP tool alike: an
  unknown `policy` name alongside a `policy_file` that overrides it, and an
  in-memory `SuppressionList` carrying no `source_sha256`. Each had already
  been fixed once on one front end and reappeared on another, so both now
  live on shared helpers — `stated_policy_base` and
  `SuppressionSource.from_loaded` — rather than being spelled inline per
  front end.
