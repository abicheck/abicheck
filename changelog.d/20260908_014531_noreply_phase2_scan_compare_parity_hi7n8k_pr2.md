### Changed

- **`compare` now runs all eleven cross-source hygiene checks
  automatically, not just six** — `header_build_context_mismatch`,
  `odr_type_variant`, `identity_collision_detected`,
  `compile_context_conflict`, and `source_surface_dso_mismatch` join
  `unversioned_exported_symbol`, `private_header_leak`,
  `exported_not_public`, `public_not_exported`, `rtti_for_internal_type`,
  and `public_to_internal_dependency` on `compare()`'s automatic,
  evolution-stated (`persistent`/`introduced`/`resolved`/`not_evaluated`)
  pipeline stage (`cross_source_checks`, still default `True`, still no
  CLI/API/Action opt-in flag — ADR-068 D4/D5). These five read build (L3)
  and source-ABI-replay (L4) evidence rather than binary/header (L0-L2)
  evidence, and evidence-gate to `not_evaluated` the same way the first six
  checks already did whenever a side lacks `--sources`/`--build-info`/
  `--depth source` evidence — no new mechanism, no new flag. `scan` is
  unaffected and not yet retired.
