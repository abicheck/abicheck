### Fixed

- `scan` no longer fabricates a `type_removed` finding when headers are given
  for one side only (`--header old=DIR`). The DWARF-vs-header-AST evidence
  shortcut is now answered per operand: a single run-wide answer left the
  header-less side with no type facts while the other had a full header AST,
  so every type read as removed. Each side now gets the best evidence it
  actually has.
