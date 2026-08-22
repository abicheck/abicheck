### Fixed

- **`abicheck.binary_utils._canonical_library_key`**: now also canonicalizes
  the Mach-O version-in-filename convention (`libfoo.1.dylib` →
  `libfoo.dylib`, `libfoo.1.2.3.dylib` → `libfoo.dylib`) — the ld64
  compatibility-version-in-filename form, distinct from ELF's
  version-after-extension `libfoo.so.N` form this function already
  handled. Previously a normal Mach-O major-version bump
  (`libfoo.1.dylib` → `libfoo.2.dylib`) was never paired by
  `compare_product_directories`'s canonical fallback, so Mach-O export,
  install-name, compatibility-version, and architecture changes were
  replaced by coarse removal/addition findings.
- **`abicheck.product_baseline.compare_product_directories`**: the
  standalone-removal/addition fallback now derives which libraries are
  genuinely unmatched from `old_map`/`new_map` (relative-path-keyed,
  collision-free) crossed against the actual pair identities, instead of
  a bare-filename set difference over the bundle-analysis maps. When two
  distinct libraries shared a basename in different directories (e.g.
  `plugins/a/plugin.so` and `plugins/b/plugin.so`) and one was removed
  while the other survived, both bundle-analysis maps collapsed to the
  identical single key, so the removal went entirely unreported.
