### Added

- **`effective_config_digest`/`effective_config_fields`** — every
  `compare`/`compare-release`/`scan --against` JSON report now carries a
  `sha256:...` fingerprint of the resolved gate/policy/surface/contract
  configuration the comparison actually ran under, plus the named field
  dict it was hashed from (so a mismatch between two reports can be
  attributed to a specific field rather than read as an opaque hash — the
  same `profile_fingerprint`/`scope_fingerprint` precedent already used
  elsewhere). Computed once, identically, for all three front ends (CLI
  cleanup phase two, "PR B"). `report_schema_version` 2.46,
  `scan_schema_version` 1.20. The field set also now covers
  `--require-complete-analysis` (`gate.require_complete_analysis`), and the
  directory/package release fan-out's `--output-dir` sibling summary
  document (`summary.json`) carries both effective-config fields too, not
  just the primary release JSON.
