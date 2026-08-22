### Documentation

- **Documented `bundle_*` finding scoping and suppression precisely, per
  detector.** [Multi-binary (bundle) ABI analysis](docs/use/multi-binary.md)
  now explains that `--scope-public-headers`/a public-surface `--policy`
  profile never filters the **graph-native** bundle detectors
  (`bundle_intra_dep_removed`, `bundle_library_removed`/`_added`, version
  drift, SONAME skew, manifest enforcement — which work from the bundle's
  own ELF resolution graph and declared contracts, not a per-library
  `DiffResult`), but *does* reach the three **diff-derived** detectors
  (`bundle_intra_dep_signature_changed`, `bundle_intra_type_changed`,
  `bundle_provider_changed`) indirectly, by filtering the already-scoped
  per-library `Change`s those detectors promote from. Also documents that
  the sibling-consumption gate is specific to `bundle_intra_dep_removed`/
  `bundle_library_removed`, not a blanket rule (`bundle_library_added` and
  `bundle_provider_changed` have no such gate; manifest/cohort findings are
  contract-driven) — and even that gate applies only inside `compare_bundle()`
  itself, since `abicheck/product_baseline.py`'s whole-product baseline
  compare reports every removed/added library unconditionally, with no
  sibling-consumption check, as a deliberate compatibility-gate fallback.
  Also documents that `compare_bundle()` is never given a suppression
  ruleset, so `--suppress` rules have no effect on any bundle-level finding
  once it fires — `--no-bundle-analysis` and `--bundle-system-providers`
  remain the only available levers (G38 Phase 1, an amendment to ADR-023).
