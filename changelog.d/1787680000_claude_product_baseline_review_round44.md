### Fixed

- **`abicheck.package.TarExtractor._safe_extract_zst_tar`**: the round-43
  decompression-bomb bound only covered the `zstandard` Python-module
  decoder path -- the external `zstd` CLI subprocess fallback (reachable
  whenever that always-installed package is somehow absent) still wrote
  its decoded output straight to disk via `-d ... -o ...`, unbounded.
  The fallback now streams through the CLI's stdout (`-dc`) with the
  same chunked, size-checked read loop as the module path, and passes
  `--memory=` to bound the CLI's own decompression window.
- **`abicheck.product_baseline.pack_product_baseline`**: two distinct
  archive member names that differ only in case (`Foo.dll`/`foo.dll`,
  or two differently-cased same-named directories) packed as separate
  members on a case-sensitive host, but unify into one entry on
  extraction to a case-insensitive filesystem (e.g. Windows),
  overwriting one and failing the manifest's own checksum for it.
  Every archived path component (files, directories, and the manifest
  member name itself) is now checked for a case-folded collision before
  publishing the archive.
