### Fixed

- **BTF/CTF parsers now reject a string-table offset outside the string
  section as incomplete evidence, instead of silently treating it as an
  empty name** — `read_null_terminated_string()` returned a bare `""` both
  for a legitimate zero-length name and for a corrupt/out-of-range
  `name_off`/`m_name_off`, with no way for a caller to tell the two apart.
  A BTF/CTF type or member whose name offset pointed outside `str_data`
  therefore either had its named layout silently dropped (an empty name
  reads as anonymous) or emitted a blank member, while
  `parse_btf_from_bytes`/`parse_ctf_from_bytes` only set
  `extraction_partial` for type-table truncation or a raised exception —
  neither of which this shape triggers — so the basic debug-evidence
  channel could still report `parsed` for a truncated/corrupt blob whose
  layout evidence was actually incomplete. `read_null_terminated_string()`
  now returns `(string, valid)`, and every name-reading extractor in both
  `btf_metadata.py` and `ctf_metadata.py` (structs, struct members, enums,
  enum members, func protos, typedefs) folds an invalid offset into
  `extraction_partial`.
- **`architecture/modules.yaml`'s `legacy_paths` inventory now rejects an
  entry naming a file that no longer exists on disk** — a deleted/moved
  module previously stayed listed indefinitely, silently pre-authorizing a
  *future* file reappearing at that exact path to bypass the
  canonical-owner/no-growth gate a genuinely new module has to clear.
  `check_architecture.py` now checks each `legacy_paths` entry against the
  real file tree.
