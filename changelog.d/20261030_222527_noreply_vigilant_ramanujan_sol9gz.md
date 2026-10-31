### Changed

- **The same-snapshot coverage note now compares evidence, not exact
  publication bytes** — ADR-062 D16's `evidence` digest
  (`abicheck/storage/evidence_digest.py`) ignores capture metadata
  (`created_at`, the dump path and mtime, the CI `build_id`) and the producer
  order of identity-matched record lists, while keeping semantically ordered
  lists (parameters, fields, enum members, `DT_NEEDED`) in. Two dumps of one
  binary taken at different times now get the "verify the correct snapshot
  files were provided" note that `compare` used to withhold. A
  `ProjectSnapshot` package records the digest as the optional
  `ArtifactRef.evidence_digest` (no `ref_schema_version` change; older
  readers ignore it), so two stored operands are matched without
  re-serializing. `snapshot_content_digest` and `same_persisted_content` are
  unchanged.
