### Fixed

- **BTF/CTF resolvers now flag an in-range type whose own kind they don't
  recognize** — a member type index that resolves to a valid type table
  entry, but one whose `kind` neither `_TypeResolver`'s name nor size
  resolution handles, previously fell through to a placeholder name
  (`"<btf_kind_N:id>"`/`"<ctf_kind_N:id>"`) and a size of `0` with no
  completeness signal, distinct from the already-fixed out-of-range-index
  case. Recognized-but-legitimately-sizeless kinds (e.g. a function type,
  which has no byte size) are not flagged — only a kind genuinely outside
  each resolver's known set is.
- **DWARF parsing now propagates a swallowed per-DIE type-resolution
  failure into extraction completeness** — a malformed `DW_AT_type`
  reference inside an otherwise-successful compilation unit (caught by
  `_resolve_type`/`_process_typedef`/`_expand_anonymous_member`/
  `_resolve_inner_type_info`, each of which returns a placeholder rather
  than raising) previously left `cu_failed` untouched, since the CU-level
  `try`/`except` only ever sees an exception that escapes every one of
  those inner catches — a run could read back `evidence_state="parsed"`
  despite silently losing a field/typedef/nested-type fact. Fixed on both
  the standalone parser (`dwarf_metadata.parse_dwarf_metadata`) and the
  unified single-pass dump path (`dwarf_unified.parse_dwarf_from_session`,
  what `dumper.py`'s real ELF dumps use) by threading a keyword-only
  `incomplete` out-param through the whole DIE-walk/type-resolution call
  chain, folded into `evidence_state` alongside the existing
  `cu_failed`/skeleton-CU check.
