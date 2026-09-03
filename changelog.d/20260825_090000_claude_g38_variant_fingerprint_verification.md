<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G38 Phase 13: `run_bundle_variant_pairing()` can now verify a captured
  variant's fingerprint against its declared coordinates.**

  `abicheck.bundle_variants_config.run_bundle_variant_pairing()` gained an
  opt-in `verify_fingerprints: bool = False` parameter. When `True`, a
  variant name present in both a declared `bundle_variants:` spec and one of
  the captured `BundleFacts` maps whose own, non-default
  `variant_fingerprint` disagrees with what the declared coordinates would
  themselves fingerprint to raises `BundleVariantsConfigError` — catching
  the wrong `BundleFacts` file being assigned to the wrong declared variant
  name, a silent misconfiguration `pair_variants()` alone can't detect since
  it pairs purely by whatever fingerprint a file happens to carry. A file
  still carrying the `DEFAULT_VARIANT_FINGERPRINT` sentinel — what every
  `--bundle-facts-out` capture produces today, since no real capture
  pipeline can be told a variant name yet — is never flagged, since it
  wasn't captured against any declared coordinates to verify against.
  Default `False` keeps every pre-existing caller unaffected. Closes the
  narrower, non-CLI-blocked half of the G38 plan's Phase 13 "Known gap"
  note; the CLI/`.abicheck.yml` surface itself remains open (see the plan
  doc).
