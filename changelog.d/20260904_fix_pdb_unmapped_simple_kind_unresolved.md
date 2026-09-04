### Fixed

- **PDB/CodeView unmapped simple type kinds no longer silently report as
  resolved** — a valid CodeView simple kind not covered by this module's
  own name/size tables (e.g. `T_HRESULT`, `0x08`) previously rendered as
  an opaque `<simple:0x..>` placeholder name with a size of 0, with no
  completeness signal — the same kind of substitution an unresolvable
  type-index reference already recorded, but this one didn't. Now
  recorded via `unresolved_type_ref_count`, independently in both the
  name and size resolvers (the two caches are populated separately, so
  either can be the first call for a given type index).
