### Fixed

- **PDB per-record parsers now report a non-exception truncated payload
  as incomplete evidence** — every individual `TypeDatabase._parse_*`
  method (`_parse_struct`, `_parse_enum`, `_parse_fieldlist`,
  `_parse_procedure`, `_parse_mfunction`, `_parse_pointer`, `_parse_array`,
  `_parse_modifier`, `_parse_bitfield`, `_parse_arglist`) and
  `_skip_subrecord` previously returned early (`return`/`break`) on a
  payload too short for its own fixed header, without raising — invisible
  to both `parse_all()`'s own exception handler and the `failed_
  record_count` signal a prior fix introduced for genuine exceptions. Each
  now returns `True`/`False` instead, propagated through `_parse_record`
  into the same `failed_record_count`, so a truncated-but-not-raising
  record (e.g. an `LF_STRUCTURE` payload shorter than 16 bytes, or a
  fieldlist cut short mid-sub-record) correctly downgrades the basic PDB
  debug-evidence channel. Also closes a latent bug this same fix
  surfaced: `LF_VFUNCTAB` had no bounds check in `_skip_subrecord` at all
  (it returned `pos + 6` unconditionally), so a truncated one could
  silently end the fieldlist parse loop with no signal whatsoever.
