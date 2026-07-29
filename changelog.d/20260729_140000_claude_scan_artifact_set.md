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
  `--against` (audit-only). New service-layer entry points
  `abicheck.service.run_scan_set`/`run_scan_set_subprocess`
  (`ScanArtifactResult`/`ScanSetResult`). Not yet wired into the MCP tool
  surface or the GitHub Action — see `docs/contribute/plans/g34-*` for what
  remains.
