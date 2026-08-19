### Changed

- **CLI cleanup phase two, PR B (slice 1)**: `compare --pack` no longer
  rejects a directory/package (release) comparison outright. A `kind: policy`
  or `kind: contract` pack's `policy.overrides`/`surface.internal_namespaces`
  contributions now apply to every library in the release fan-out, and to its
  build-configuration-matrix findings, the same way they already applied to a
  single-pair `compare` and to `scan --against`. Resolved once per invocation
  and applied uniformly (`CompareRequest.pack_policy_overrides`/
  `pack_internal_namespaces`, folded by `service_compare_pipeline.
  classify_compare_pair`), so every library gets the identical pack-resolved
  policy. A `kind: gate` pack (`gate.exit_code_scheme`/`gate.severity.*`) is
  still rejected for a release comparison, with a specific error explaining
  why, since the release fan-out has no resolved gate-options wiring for it
  yet -- unchanged from before this change, and still true for `scan
  --against` too. No change to a single-pair `compare`'s existing `--pack`
  behavior.
