### Fixed

- **`abicheck.product_baseline._discover_library_map`**: recognizes a
  Mach-O dynamic library/bundle or a PE/COFF DLL by content, not just by
  filename suffix — an extensionless macOS framework binary
  (`Foo.framework/Foo`) or a Windows `.pyd` Python extension module
  previously never entered discovery at all, so a framework- or
  extension-module-only product silently compared as `NO_CHANGE` with no
  per-library comparisons run. Mirrors the existing ELF `ET_DYN`
  content-aware fallback; a Mach-O executable (`MH_EXECUTE`) or a plain
  `.exe` are correctly still excluded. Deliberately conservative for a fat
  (universal) Mach-O archive — see `_macho_is_library_content`'s own
  docstring for why.
- **`abicheck.product_baseline._discover_library_map`**: rejects a
  library-shaped symlink whose target resolves outside the product root
  entirely (`libfoo.so -> /usr/lib/libfoo.so`) instead of discovering and
  later comparing the host file it points at — that made the comparison
  machine-dependent and could silently hide a library the product should
  have shipped but didn't. Matches the containment discipline pack/unpack
  already enforce for a manifest-declared path.
- **`abicheck.product_baseline._discover_library_map`**: no longer
  discovers a conventional `objcopy --only-keep-debug` split-debug
  sidecar (`libfoo.so.1.debug`) as its own library — it is itself a valid
  ELF file retaining its original binary's `ET_DYN` header, so the
  content-aware ELF fallback previously discovered it as a second,
  independent library alongside the real DSO it was split from, and a
  release merely omitting or relocating the sidecar read as a breaking
  removal of an unchanged library.
- **`abicheck.binary_utils._canonical_library_key`**: case-folding is now
  restricted to the PE/`.dll` suffix (the one format whose loader identity
  is genuinely case-insensitive) instead of applying to every format
  alike. An ELF `libFoo.so` -> `libfoo.so` or Mach-O `libFoo.dylib` ->
  `libfoo.dylib` case-only rename previously paired via this same
  canonical fallback and had its removal/addition silently suppressed —
  but an existing consumer whose `DT_NEEDED`/`LC_LOAD_DYLIB` still names
  the old spelling fails to load the renamed file on a case-sensitive
  loader, a real break this canonical pairing must not hide.
