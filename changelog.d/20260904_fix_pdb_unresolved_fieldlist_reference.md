### Fixed

- **PDB parsing now distinguishes a struct/enum with zero members from one
  whose member-list reference is unresolvable** — a fully-framed
  `LF_STRUCTURE`/`LF_UNION`/`LF_ENUM` whose `field_list_ti` names a type
  index the TPI stream never defined, or one that resolves to a real
  record that isn't `LF_FIELDLIST`, previously collapsed to the same
  empty `TypeDatabase.get_fieldlist()` result as a record that
  legitimately has no fields (`field_list_ti == 0`) — silently emitting
  an empty layout with no completeness signal. `TypeDatabase` gains
  `has_fieldlist()` so callers can tell the two apart: an affected struct
  is now dropped (the same per-record skip-and-continue as any other
  malformed-field extraction failure) and an affected enum keeps its own
  name/underlying size but not a fabricated empty member list, with the
  basic debug-evidence channel downgraded to `partial` either way.
