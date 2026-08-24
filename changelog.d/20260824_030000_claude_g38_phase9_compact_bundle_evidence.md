<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G38 bundle analysis: `compare --release` no longer retains every
  library's full `AbiSnapshot` for the whole release when only the bundle
  layer's signature-evidence gate needs it.** Wiring G38 Phase 4 into the
  live `compare --release` path made `collect_diff_results=True` the
  default for every directory/package comparison (not only when
  `--bundle-facts-out`/JUnit was requested), so every completed library's
  full old+new `AbiSnapshot` (functions, types, layouts, source graph,
  build-source evidence, everything) was retained until the whole release
  finished and bundle analysis ran — a real memory regression relative to
  the pre-Phase-4 default. Fixed with a new, frozen
  `bundle_models.BundleSignatureEvidence` projection carrying only the
  three fields `find_unverified_signature_findings` actually reads
  (`function_map`, `variable_map`, `elf_only_mode`); a default
  `compare --release` (bundle analysis on, no JUnit/`--bundle-facts-out`)
  now stashes this compact projection instead of the full snapshot, so the
  rest of each snapshot becomes eligible for garbage collection once the
  per-library comparison returns. JUnit and `--bundle-facts-out`, which do
  need the real `AbiSnapshot`, are unaffected.
