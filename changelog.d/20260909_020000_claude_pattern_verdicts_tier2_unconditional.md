<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->
### Fixed

- Pattern-verdict modulation (ADR-068 D4) is unconditional for the native
  `compare` CLI and the directory/package release fan-out, but the shared
  Tier-2 classification chokepoint (`service.run_compare`/
  `run_compare_request`, and the stored-BundleFacts comparison drivers)
  still forwarded a request's own `pattern_verdicts` field, which defaults
  to `False`. A typed API caller building a bare `CompareRequest`, or one
  of the stored-BundleFacts drivers, could therefore reach compatibility
  policy with modulation disabled — an equivalent scalar `compare` and an
  equivalent typed-API call could disagree on the verdict for the same
  snapshots. Modulation is now forced unconditionally at every one of
  these call sites, matching the treatment `surface_metrics` already gets.
