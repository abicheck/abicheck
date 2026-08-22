### Fixed

- **`abicheck.product_baseline.pack_product_baseline`**: symlink-target
  validation used `os.path.isabs()` alone, which is host-dependent --
  packing on POSIX, it doesn't recognize a Windows drive-absolute target
  (`C:\outside\foo.dll`) or a UNC path as absolute at all, and the
  existing POSIX containment check (backslashes being ordinary
  characters on POSIX) treats it as an ordinary in-tree relative
  filename too. The archive packed "portably" while `TarExtractor`
  correctly refuses the same target as escaping at unpack time on
  Windows, where it really is absolute. The identical gap let a
  relative target spelled with backslash `..` components
  (`..\..\outside.so`) pack cleanly on POSIX (parsed as one opaque
  filename, no traversal detected) while genuinely walking above the
  archive root once interpreted with real Windows path separators.
  Both are now rejected at pack time: an absolute check now also
  recognizes Windows drive-absolute/UNC syntax, and a new purely-lexical
  containment check simulates Windows-separator resolution to catch a
  backslash-based traversal no POSIX-only check can see.
