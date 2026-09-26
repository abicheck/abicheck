### Fixed

- A directory/package `compare` now forwards a stated language
  (`--lang` / `compile.lang`) to every member comparison as *stated*, so a
  one-member release yields the same findings as the scalar comparison; before,
  members auto-detected and could parse an ambiguous header as C.
