### Fixed

- **BTF/CTF parsers now reject a header shorter than its own fixed size**
  (`hdr_len < 24` for BTF) — previously accepted outright, with section
  offsets then computed relative to that bogus, too-small `hdr_len`; a
  malformed header (e.g. `hdr_len=0`) could silently produce an empty or
  garbage parse while still reporting `has_btf=True` and
  `extraction_partial=False`.
- **BTF/CTF `_parse_types()` now validates every record's `kind` at the
  table level, not on demand** — an unsupported kind's real extra-data
  size is unknowable, so the previous "no extra data" fallback for it
  could misalign every subsequent record's own offset in the type table,
  corrupting facts far beyond the one unsupported record. A standalone
  unsupported-kind record nothing else references was previously invisible
  to every extractor's own completeness signal entirely (no
  struct/enum/func_proto/typedef ever iterates it). Parsing now stops at
  an unsupported kind (matching the existing truncation contract) instead
  of continuing past a record whose true size can't be determined.
