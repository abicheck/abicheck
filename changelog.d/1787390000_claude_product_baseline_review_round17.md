### Fixed

- **`abicheck.product_baseline.compare_product_directories`**: four more
  correctness findings from continued review of this same PR.
  - A library matched only via the canonical (SONAME-major-stripped,
    case-insensitive) fallback — not an exact relative-path match — is no
    longer reported as a standalone removal. Its own bare filename
    genuinely differs between the old and new sides (a SONAME major bump
    such as `libfoo.so.1` -> `libfoo.so.2`, or a case-only rename such as
    `Foo.dll` -> `foo.dll`), so a naive bundle-map set difference read it
    as a removal even though the per-library ABI comparison had already
    run for that exact library.
  - `_roots_for_library` now rejects a bare `str` outright (both a flat
    `header_roots="include"` and a per-library mapping value spelled the
    same way) instead of silently accepting it: a `str` satisfies the
    declared `Sequence[str]` type, so it was previously iterated
    character-by-character, with every single-character candidate failing
    `.is_dir()` and silently running the comparison with zero header
    evidence.
  - `_discover_library_map` now also recognizes an extensionless ELF
    shared object (a plugin with no conventional `.so` suffix, real on
    Linux) via the same content-aware `ET_DYN` sniff
    `abicheck.package.discover_shared_libraries` already uses for its own
    ELF-only walk — supplementing, not replacing, the existing
    suffix-based check that still covers `.dll`/`.dylib`. Previously such
    a library was invisible to both the per-library and bundle-level
    comparison, so two products that both changed it could still compare
    as `NO_CHANGE`.
  - The directory walk now sorts `dirnames` in place (not just
    `filenames` within one directory), so a hardlink/symlink alias whose
    target lives in a different directory always resolves to the same
    surviving representative regardless of the filesystem's own
    (unspecified) sibling-directory order.
