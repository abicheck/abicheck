<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G38 bundle analysis: a per-library diff's canonical provider key is now
  resolved against both bundle sides, not only the new one.**
  `compare_bundle()`'s `diff_by_library` canonicalized `DiffResult.library`
  (a real, possibly SONAME-versioned on-disk basename) against only the
  new-side bundle snapshot's own basename map. `checker.compare()` sets
  `DiffResult.library` from the *old* side, so a provider whose versioned
  filename changed between old and new (e.g. `libcore.so.1.2` →
  `libcore.so.1.3`) had a basename the new-side map could never resolve —
  the promotion/type-change/provider-change detectors silently never fired
  for that provider. Fixed by merging both sides' basename maps (new-side
  wins a collision, since it's what the resolution graph these keys feed
  into was actually built from).
