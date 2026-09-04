### Fixed

- **PDB parsing now flags an unresolvable member/enum-underlying/pointee
  type reference** — a valid `LF_MEMBER`/`LF_ENUM` whose `type_ti`/
  `utype_ti` (or a wrapping `LF_POINTER`/`LF_MODIFIER`/`LF_ARRAY` naming
  one) referenced no known TPI record previously fell through
  `TypeDatabase.type_name()`/`type_size()` to a `"<ti:0x...>"` placeholder
  and a size of 0 with no completeness signal — one layer below the
  already-fixed field-list-reference gap. `TypeDatabase` gains an
  `unresolved_type_ref_count` property, checked by `parse_pdb_debug_info`
  after struct/enum extraction to downgrade the basic debug-evidence
  channel to `partial`.
- **DWARF advanced-channel (value-ABI-trait) extraction now propagates a
  swallowed per-DIE type-resolution failure** — a malformed `DW_AT_type`
  on an exported function's return or parameter type, caught deep inside
  the value-ABI-trait walk (`resolve_type_die`/`_unwrap_qualifiers`/
  `_is_nontrivial_aggregate`/`_type_unaligned_at`, each returning a
  placeholder rather than raising), previously left the advanced channel
  at `evidence_state="parsed"` while silently omitting that function's
  `value_abi_traits`/`return_value_sizes`/`return_memory_classified`
  entries. Fixed on both `dwarf_advanced.parse_advanced_dwarf` (the
  standalone entry point) and `dwarf_unified.parse_dwarf_from_session`
  (the unified path real ELF dumps use), mirroring the equivalent basic-
  channel fix.
- **CTF parsing now rejects a compressed stream truncated at its end
  marker** — `zlib.decompressobj().decompress()` can return a complete-
  looking payload without raising even when the input was cut short by as
  little as one byte (removing only the trailing checksum), since
  decompression finishes before that marker is consumed; `_decompress_if_
  needed()` now checks `decompressor.eof` and raises when the stream never
  properly terminated, instead of silently reporting complete evidence for
  a truncated `.ctf` section.
