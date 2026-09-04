### Fixed

- **BTF/CTF resolved-type lookups now propagate an out-of-range type
  reference into extraction completeness** — a struct/union member whose
  own type index (`m_type`) names an index past the parsed type table
  previously resolved silently to a `"<btf:N>"`/`"<ctf:N>"` placeholder
  name and a size of `0`, with no signal reaching `extraction_partial` —
  the member's own name may be perfectly valid, so no direct extractor's
  completeness tracking ever observed this failure. `_TypeResolver` now
  records this shape (an unresolved `type_id`) in the same shared
  accumulator it already uses for an invalid string offset.
- **PDB fieldlist parsing now rejects a trailing partial sub-record tag
  instead of silently exiting the loop** — `_parse_fieldlist()`'s own
  `while pos + 2 <= len(d)` guard never examined a tail shorter than 2
  bytes, so a sub-record whose 2-byte leaf tag was itself cut to 0 or 1
  bytes reached the end of the buffer unnoticed, reporting the fieldlist
  complete. A post-loop pass now consumes any legitimate trailing
  `LF_PAD*` byte(s) and flags anything left over — including a pad claim
  overshooting the buffer — as truncated.
