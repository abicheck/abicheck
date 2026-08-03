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
