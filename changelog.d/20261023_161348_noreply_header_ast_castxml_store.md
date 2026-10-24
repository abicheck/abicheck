### Changed

- **Castxml header-AST cache is now a view over the per-invocation parse store** (Phase 6 of the L4/L2 extraction convergence plan). Castxml entries are `<key>.json` parse-store entries keyed by content digests of the headers and include roots instead of their mtimes, so a touched-but-unedited header keeps its cached AST and an edited one misses. The disk entry format changes, so the first run after upgrading is a cold run; the old `<key>.xml` entries are never read again. `read_cached_castxml` is removed. Clang entries and their key are unchanged.
