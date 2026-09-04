### Fixed

- **DWARF anonymous struct/union members with no `DW_AT_type` now flag
  incompleteness** — `_expand_anonymous_member`'s own type-less branch
  previously returned an empty member list with no completeness signal,
  silently dropping that member's nested layout while `evidence_state`
  still reported `"parsed"`. A real anonymous aggregate member always
  names its own struct/union type, so this is malformed debug info, not
  a legitimate type-less case — the identical gap `_process_member`'s
  own named-member equivalent was already fixed for.
- **A genuine DWARF session-open/parse failure is now distinguished from
  legitimate absence of debug info** — `open_dwarf_session` (and its
  `parse_dwarf` caller), `parse_dwarf_metadata`, and
  `parse_advanced_dwarf` previously reported `evidence_state =
  "not_available"` both when a binary genuinely had no DWARF and when
  `ELFFile`/`get_dwarf_info()` raised on a malformed or corrupt ELF —
  collapsing "nothing to try" and "we tried and it broke" onto the same
  value. `open_dwarf_session` now accepts an opt-in `open_failed`
  out-param appended only when the ELFFile-construction/`get_dwarf_info`
  call itself fails (not on file-open or not-a-regular-file), and
  `parse_dwarf` reports `evidence_state = "failed"` for both metadata
  objects when that happens. The two standalone entry points,
  `parse_dwarf_metadata` and `parse_advanced_dwarf`, now report
  `evidence_state = "failed"` from their own top-level
  `except (ELFError, OSError, ValueError)` clause.
