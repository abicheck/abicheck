### Fixed

- **DWARF CFI coverage now recognizes alternate entry points** — an
  exported function symbol placed a few bytes inside another function's
  FDE range (a real shape in hand-written assembly libraries, e.g. a
  fast-path variant sharing its slow-path sibling's unwind info) was
  previously always reported as uncovered, since the coverage check only
  matched a symbol's address against an FDE's exact `initial_location`.
  Now matched against each FDE's own covered address range too.
- **A malformed BTF/CTF section is no longer indistinguishable from an
  absent one** — `BtfMetadata`/`CtfMetadata.to_dwarf_metadata()` mapped
  an existing-but-unparseable section (bad header, section bounds
  exceeding the data, a corrupt type table) to `evidence_state =
  "not_available"`, the same value a binary with no such section at all
  produces — so an explicitly requested extractor
  (`--debug-format btf`/`ctf`) that failed read back as having simply had
  nothing to extract. Both now carry a distinct `extraction_failed` flag,
  mapped to `evidence_state = "failed"`.
