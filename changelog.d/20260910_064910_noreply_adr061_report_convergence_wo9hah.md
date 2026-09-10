### Fixed

- **`ReportEnvelope` snapshotting now closes the whole class of shared
  mutable custom objects on `DiffResult`**, not one field at a time —
  `_snapshot_diff_result`'s catch-all now deep-copies every non-primitive
  attribute (`old_metadata`/`new_metadata`, `contract_context`, and every
  other `object`-typed field), except `disposition_ledger` (remapped by
  identity instead) and a value the stdlib itself cannot deep-copy (e.g. a
  frozen dataclass built over an immutable `mappingproxy`, which is
  already safe to share as-is). Previously `comparability_assurance`,
  `policy_file`, and `old_metadata`/`new_metadata` had each been fixed
  individually as they were reported; this generalizes the fix so a new
  mutable field added to `DiffResult` in the future is covered
  automatically.
