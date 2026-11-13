### Fixed

- **Corrupt or truncated inputs no longer read as a clean comparison** — a
  PE whose export directory, export names or sections run past the end of
  the file, a Mach-O whose symbol table, string table or export trie is cut,
  and an ELF whose DWARF is present but unreadable (a corrupt
  `.debug_abbrev`, a lost `.debug_info` section header, a failed compilation
  unit) used to be read as a smaller surface: two identically damaged copies
  compared `NO_CHANGE` (exit 0), and a damaged OLD side read its lost types
  as added (`COMPATIBLE`, exit 0). `dump` and `compare` now refuse such an
  input with exit 1 and say why (a detached `--debug-root` file that
  cannot be opened is refused the same way); a directory compare reports the member as
  `ERROR` (exit 4) without an "unexpected failure" traceback. Present but
  unreadable DWARF refuses the whole dump rather than comparing a partial
  type set; to compare the exported symbols alone, strip the debug sections
  (`strip -g`). A malformed storage-v2 snapshot now raises the new
  `MalformedSnapshotError` from `load_snapshot` (or on first `declarations`
  access) instead of a bare `KeyError`/`TypeError`/`AttributeError`; it is a
  `SnapshotError` and still a `TypeError`/`ValueError`, so existing handlers
  keep catching it.
- **A corrupt DWARF sibling reference no longer hangs a dump** — a
  `DW_AT_sibling` pointing back at its own entry (or out of its unit) made
  the debug-info walk loop forever; it is now refused as an unreadable unit.
