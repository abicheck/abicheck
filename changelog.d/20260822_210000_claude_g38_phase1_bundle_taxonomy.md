### Documentation

- **Documented `bundle_*` finding scoping and suppression precisely, per
  detector.** [Multi-binary (bundle) ABI analysis](docs/use/multi-binary.md)
  now explains that scoping (`--scope-public-headers`, which removes findings
  from `DiffResult.changes`) and policy (`--policy`'s `overrides:`, which only
  reclassifies a kind's verdict and never removes a finding) are two separate
  mechanisms — a distinction the page previously conflated by describing a
  "public-surface `--policy` profile" as if it filtered findings. Explains
  that `--scope-public-headers` never filters the **graph-native** bundle
  detectors (`bundle_intra_dep_removed`, `bundle_library_removed`/`_added`,
  version drift, SONAME skew, manifest enforcement — which work from the
  bundle's own ELF resolution graph and declared contracts, not a per-library
  `DiffResult`), but *does* reach the three **diff-derived** detectors
  (`bundle_intra_dep_signature_changed`, `bundle_intra_type_changed`,
  `bundle_provider_changed`) indirectly, by filtering the already-scoped
  per-library `Change`s those detectors promote from — and, since
  `--suppress` is applied to the same per-library `DiffResult` upstream of
  `compare_bundle()`, a suppression rule reaches those same three diff-derived
  kinds the identical way, even though no `bundle_*` kind can be suppressed
  *directly* by name. Documents that the sibling-consumption gate covers five
  kinds — `bundle_intra_dep_removed`, `bundle_library_removed`,
  `bundle_intra_dep_signature_changed`, `bundle_intra_type_changed`, and
  `bundle_intra_dep_resolved_to_different_version` — but not
  `bundle_library_added` or `bundle_provider_changed` (contract-driven
  manifest/cohort findings are a third, ungated category), and that even the
  gate on `bundle_library_removed` applies only inside `compare_bundle()`
  itself, since `abicheck/product_baseline.py`'s whole-product baseline
  compare reports every removed/added library unconditionally, with no
  sibling-consumption check, as a deliberate compatibility-gate fallback.
  For the graph-native kinds, which have no per-library `Change` to starve
  upstream, `--no-bundle-analysis` and `--bundle-system-providers` remain the
  only available levers (G38 Phase 1, an amendment to ADR-023).
