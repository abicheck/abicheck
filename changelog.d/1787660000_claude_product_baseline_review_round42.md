### Fixed

- **`abicheck.product_baseline.pack_product_baseline`**: an archive
  member name could still decompose safely under Windows path
  separators (no drive/root anchor, no ``..``) while still being a name
  Windows cannot represent at all -- a reserved device name (``CON``,
  ``aux.txt``, ...), a forbidden character (``:*?"<>|`` or a control
  character), or a trailing dot/space Windows silently strips (making
  ``name.`` and ``name`` collide). `_validate_portable_arcname()` now
  checks every path component against all three.
- **`abicheck.product_baseline.unpack_product_baseline`**: the manifest
  inventory check only ever caught an *undeclared* library -- an
  archive could declare an ordinary file (``README.txt``) as a
  `LibraryEntry`, with a correct size/digest for that exact file, and
  this validation accepted it, handing every caller of
  `manifest.libraries` something falsely advertised as a library. Every
  declared library path is now also validated with `_is_library_path()`,
  the same predicate packing and discovery already use.
- **`abicheck.product_baseline._iter_discovered_libraries`**: a
  library-shaped symlink with a missing target made the fallback
  identity lookup fail with `ENOENT`, which fell through to yielding the
  path as a discovered library anyway -- comparing a product containing
  only such a dangling alias against an empty product produced a
  spurious `bundle_library_removed` finding instead of the alias being
  silently skipped, the same as a target resolving outside the product
  root already is.
