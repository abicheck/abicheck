### Fixed

- **Header-free Mach-O dumps are keyed on the adjacent `.dSYM`** — the dump cache hashed a Mach-O binary's bytes but not the DWARF bundle beside it, so a dump made before a `<lib>.dSYM` existed (or after it changed) could be served from the cache for a dump that should read that bundle. The adjacent bundle's DWARF file is now a content-hashed key input, as a PE's PDB already was. Adding, replacing or removing the bundle now misses the cache.
