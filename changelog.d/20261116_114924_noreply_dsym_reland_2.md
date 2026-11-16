### Fixed

- **Header-free Mach-O dumps are keyed on the adjacent `.dSYM`** — the dump cache hashed a Mach-O binary's bytes but not the DWARF bundle beside it, so a dump made before a `<lib>.dSYM` existed (or after it changed) could be served from the cache for a dump that should read that bundle. The adjacent bundle's DWARF file is now a content-hashed key input, as a PE's PDB already was. Adding, replacing or removing the bundle now misses the cache.
- **A header-free Mach-O dump reads the adjacent dSYM too** — the CLI's header-free Mach-O dump was built by the workflow extractor, which never read DWARF, while a dump with headers did. Both now make the same DWARF decision from the adjacent `<lib>.dSYM`, so the presence of headers no longer changes what a Mach-O snapshot records.
