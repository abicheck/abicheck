### Fixed

- **PDB type database no longer keeps a half-parsed state.** `TypeDatabase.parse_all`
  marked itself parsed before parsing, so an unexpected error from any step
  left a partly filled type table that every later call treated as complete.
  It now marks itself parsed only after success and starts over on retry.
- **Bazel `--build-info` extractor status.** A full collection from a
  pre-captured Bazel query was recorded with status `present`, a value no
  other extractor uses and no consumer recognises; it is now `ok`. Stored
  records with `present` load as `ok`.
- The pattern-facts C lexer rejects an unknown internal state instead of
  scanning it as a character literal.

### Changed

- Extractor records reject a status other than `ok`, `partial`, `failed` or
  `skipped`. A stored record with an unrecognized status now loads as `failed`
  with a diagnostic instead of being kept as an unknown string.

- The CodeView record dataclasses (`CvStruct`, `CvEnum`, `CvMember`, …,
  `CvBitfield`) now live in `abicheck.extract.pdb_codeview_types`; import them
  from there rather than from `abicheck.pdb_parser`.
