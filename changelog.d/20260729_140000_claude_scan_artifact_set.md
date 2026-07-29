<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`scan --artifact-set`** (ADR-056/G34): audit a *set* of libraries — a
  directory or an explicit comma-separated path list — as one operation,
  with no old side to diff against. Each member gets the same always-on
  tier + pinned evidence level a single-binary `scan` runs, plus one new
  cross-library bundle-audit pass that flags an unresolved intra-set
  dependency (`bundle_unresolved_intra_dependency`, `COMPATIBLE_WITH_RISK`):
  a library in the set importing a symbol that no library in the set
  exports and that isn't covered by the built-in or `--bundle-system-providers`
  allow-list. Mutually exclusive with the positional `ARTIFACT` and with
  `--against` (audit-only). Forwards the same L2 compile-context flags
  (`--gcc-path`/`--sysroot`/`--nostdinc`/etc.) the single-binary path
  already resolves, so a cross-toolchain artifact-set scan doesn't
  silently parse headers against the host toolchain. New service-layer
  entry points `abicheck.service.run_scan_set`/`run_scan_set_subprocess`
  (`ScanArtifactResult`/`ScanSetResult`); `ScanSetResult.to_dict()`'s
  `bundle_findings` carries the full finding records (kind/symbol/
  consumer/provider/description), with a separate `bundle_finding_count`
  for the count alone. Not yet wired into the MCP tool surface or the
  GitHub Action — see `docs/contribute/plans/g34-*` for what remains.

### Changed

- **`SCAN_SCHEMA_VERSION` bumped to `1.5`**: `ScanSetResult.to_dict()`
  (the new `scan --artifact-set` aggregate payload) carries the same
  `scan_schema_version` marker as `ScanResult.to_dict()` but is a
  structurally distinct shape (`per_artifact`/`bundle_findings`/
  `bundle_verdict`/`bundle_incomplete` instead of `findings`/`layers`/
  `confidence`/`estimate`/`report`); the version bump makes that a
  detectable contract rather than an undocumented addition under an
  unchanged marker. Bumped to `1.5`, not `1.4`, since `1.4` was
  independently claimed by the ADR-049 Phase 5 suppression-block addition
  that landed on `main` first.
