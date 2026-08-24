<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G38 bundle analysis: two more correctness gaps in the confirmed-boundary-
  break promotion path, found by review after the fix above.**
  1. `bundle._detect_intra_dep_signature_changed`'s `diff_by_library` lookup
     previously keyed on `DiffResult.library`'s raw, possibly SONAME-versioned
     on-disk basename (e.g. `libcore.so.1.2.3`), while
     `BundleSnapshot.resolution` keys every provider/consumer by the
     version-stripped bundle-canonical name (`libcore.so`) — so a promoted
     finding could silently never fire, or attribute
     `provider_library="libcore.so.1.2.3"` instead of the canonical name, for
     any normally-versioned library. Fixed by sharing one
     `bundle_models.basename_to_bundle_key()` function between `bundle.py`
     and `bundle_signature_evidence.py` (which already had the identical fix
     for its own, separate lookup).
  2. Widening promotion to cover `calling_convention_changed` reached a kind
     with a real `plugin_abi` policy demotion (`COMPATIBLE`, for a plugin and
     its host rebuilt together from the same toolchain) — but the promoted
     `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED` bundle finding had no policy
     sensitivity, always `BREAKING` regardless of the caller's selected
     policy. `_detect_intra_dep_signature_changed` now takes the same
     `policy` string `compare_bundle()` already receives and skips promoting
     a change whose effective category under that policy isn't `BREAKING`.
