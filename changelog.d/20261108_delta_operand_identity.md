### Added

- **`delta-baseline` pairs the snapshot with the PR's library by identity** —
  `actions/main-snapshot` now uploads `operand.json` (the library path as
  given) beside the snapshot, and the main Action checks, before the delta
  comparison, that the snapshot is the same library as `new-library`: same
  SONAME/install name, a SONAME version bump, or the same path/file name.
  A renamed or relocated library is paired with the new `delta-operand-map`
  input (`SNAPSHOT=PR` lines). A snapshot of a different library is never
  compared (it would read as every symbol removed); the comment says
  `operand_unmatched`, `operand_ambiguous`, `operand_unreadable` or
  `operand_map_invalid` and shows
  the release comparison. New output `delta-operand-match`. See
  `docs/reference/main-snapshot.md#operand-identity-is-the-snapshot-this-prs-library`.
