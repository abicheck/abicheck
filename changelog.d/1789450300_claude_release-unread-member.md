### Fixed

- **An unread release member no longer turns its obligations into missing
  exports.** `compare/bundle_export_index.member_export_names` (and the
  compact per-member evidence the release fan-out keeps) read a member whose
  platform block was never parsed -- a default or parse-failed block, no
  entries and no header fields -- as a member exporting nothing, so the
  release reconciliation reported every obligation only that member provides
  as `missing_export`. Both now read through one rule,
  `model.export_index.read_default_export_names` (the same "was this table
  read" test the export fact and the `exports` coverage record use): an
  unread member is recorded in `members_without_exports`, the side's
  coverage is incomplete, and its obligations are `unresolved`. A parsed
  member that genuinely exports nothing is still a complete, empty table.
