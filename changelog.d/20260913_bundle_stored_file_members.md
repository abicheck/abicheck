### Fixed

- Bundle analysis: a release member supplied as a *loose* stored snapshot file
  (`.abicheck.json`, `.json.gz`, `.json.zst`) is now resolved into the
  cross-DSO bundle graph, like a directory-backed `ProjectSnapshot` package
  already was. Previously `build_bundle_snapshot_mixed` recognised only
  directories, so a file-backed member fell through to the ELF parser and was
  dropped as "not ELF" — silently emptying the OLD-side graph, degrading every
  cross-DSO check (provider migration, intra-dependency symbol removal, SONAME
  skew) to "every library in this release is new", and reporting a
  *compatible* bundle verdict carried by phantom `bundle_library_added`
  findings. Detection is by content (magic bytes / JSON object opener), never
  by filename suffix.
