### Fixed

- **PDB comparisons no longer report `parsed` basic debug evidence when the
  TPI type stream was silently truncated or an individual record failed to
  decode** — `parse_tpi_stream()` could previously stop before consuming
  every type index the stream header promised (a malformed record length,
  a record whose declared length ran past the stream's own bounds, or
  simply running out of bytes), and `TypeDatabase.parse_all()` caught
  per-record decode failures without exposing either condition to any
  caller — both only logged at debug level. `TpiStream` gained a
  `truncated` field and `TypeDatabase` gained `failed_record_count`, both
  now read by `parse_pdb_debug_info` to downgrade the basic PDB channel to
  `partial` instead of silently claiming complete layout/enum evidence.
