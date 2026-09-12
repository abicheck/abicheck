### Fixed

- The schema-staleness self-compare check now asks the real snapshot
  serializer whether two operands are the same persisted content, instead of
  reimplementing that projection field by field. Snapshots that serialize
  identically but whose in-memory fields differ — a mapping the codec
  reprojects through fixed keys, a rounded float, a derived id, an aliased
  graph — no longer report degraded assurance, and can no longer fail
  `--require-complete-analysis`, for a difference the stored form does not
  record.
