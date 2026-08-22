### Fixed

- **`abicheck.product_baseline.compare_product_directories`**: round 34's
  directory-scoped canonical fallback fixed the cross-directory basename
  collision it targeted, but replaced the previous *global* fallback
  outright instead of layering on top of it -- so a single library that
  moves directories between releases (`lib/provider.so` becoming
  `lib64/provider.so`, version bump included) had no shared directory
  left for the fallback to key on at all, silently reporting an
  unchanged library as a removal plus addition. The fallback is now two
  ambiguity-safe passes: a directory-scoped pass first (closing round
  34's gap), then a bare, global canonical pass over whatever the first
  pass left unpaired (restoring the pre-round-34 directory-move
  behavior), each grouping the complete per-side discovery so an
  earlier pass consuming one member of an ambiguous group never makes
  the remaining members look artificially unambiguous.
- **`abicheck.product_baseline._discover_library_map`**: a
  library-shaped self-referential symlink (`loop.so -> loop.so`) made
  its direct `Path.resolve()` call raise `RuntimeError`, which its
  `except OSError` guard did not catch -- the raw exception escaped
  `_discover_library_map()` and therefore `compare_product_directories()`
  instead of the loop being treated as any other unresolvable path.
- **`abicheck.product_baseline._add_member`** (the `pack_product_baseline`
  packing path): the identical self-referential-symlink shape made a
  direct `Path.resolve()` call here raise `RuntimeError` too, escaping
  past the function's own `except ValueError` (target-escape) handling
  and bypassing the documented `SnapshotError`-only contract while
  packing. Both now raise `SnapshotError` for a genuine symlink loop.
