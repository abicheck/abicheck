### Changed

- Under `--contract public`, the removal or addition of a symbol only the
  export table knew -- an accidental export no public header declares -- is
  now classified `UNKNOWN_UNPROVEN` / `closed_domain_no_commitment` (outside
  the declared contract) when the public headers' raw text never spells the
  symbol's name in any preprocessor branch. Before, it was always
  `UNKNOWN_UNRESOLVED`. A declaration inside an inactive `#if` branch keeps
  it unresolved (catalog case97), and without `--contract` nothing changes
  (catalog case182 stays BREAKING).
- Snapshot schema v55: header-derived snapshots carry
  `public_header_identifiers_fact`, the identifier tokens of the public
  header set's raw text (comments stripped, every `#if` branch included),
  with a stated reason when it could not be captured (no header set, an
  unreadable header, or `##` token pasting). Older snapshots load it as
  not collected.

### Fixed

- The whole-snapshot disk cache key now includes the snapshot schema
  version, so a cache entry written before a schema change is never served
  as a current snapshot that merely lacks the new field.
