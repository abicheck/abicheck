### Fixed

- **`compare()`'s automatic pattern/preprocessor pre-scan no longer reports
  `introduced`/`resolved` from a partially-covered scan.** A side with
  skipped pattern files or a preprocessor probe that failed or was
  truncated by the probe cap now folds as `not_evaluated`, matching the
  already-established rule for a side with no evidence at all — a
  construct or macro divergence absent only because of incomplete coverage
  must not read as a confirmed addition or removal.
