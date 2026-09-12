### Fixed

- A declaration recording a declaring file no longer reports
  `declaration_moved` when an unrelated nested marker changes: the recorded
  file's own marker must be one of the markers that changed.
- Two closure markers that trade places *and* shift coordinates are reported
  as `declaration_identity_reconciled` again, rather than as a coordinate-only
  shift claiming no material identity change.
