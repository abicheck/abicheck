### Fixed

- **`abicheck.product_baseline.pack_product_baseline`**: a literal
  backslash inside an archive member name or a relative symlink target
  (a valid POSIX filename/target character) is always reinterpreted as
  a path separator once unpacked on Windows, even when the resulting
  decomposition is neither anchored nor traversing (`foo\bar.so`
  restores as `foo/bar.so`, `sub\file.so` as a two-component target) --
  the existing checks only rejected a dangerous decomposition, not a
  safe-but-different one. Any backslash is now rejected outright.
- **`abicheck.product_baseline.unpack_product_baseline`**: an untrusted
  manifest's `header_roots`/library `path` containing an embedded NUL
  byte made `Path.resolve()` raise `ValueError` from `_resolve_under_root`,
  which only caught `RuntimeError` (a symlink loop) -- the raw exception
  escaped past the documented `SnapshotError`-only contract. Now caught
  and translated the same way.
