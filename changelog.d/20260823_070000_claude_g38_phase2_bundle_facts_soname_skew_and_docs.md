### Fixed

- **A stored-baseline `compare_bundle_from_facts()` comparison could miss
  `bundle_soname_skew` for a versioned DSO with no `DT_SONAME` (Codex
  review, fresh evidence).** `bundle._detect_soname_skew()` falls back to a
  library's real on-disk filename (e.g. `libfoo_core.so.1`) to derive its
  SONAME major when `DT_SONAME` itself is absent — but
  `bundle_snapshot_from_facts()` had no real filename to reconstruct with,
  so it synthesized `Path(canonical_key)` (`libfoo_core.so`, unversioned),
  from which no major is derivable. `BundleFacts` now also persists each
  library's real on-disk filename (`library_filenames`, captured alongside
  the existing `filesystem_aliases`) and threads it through reconstruction,
  so a stored-baseline comparison catches the identical
  `bundle_soname_skew` a live comparison would.
- **The G38 Phase 2 stored-baseline doc example still missed a
  runtime-only versioned DSO (Codex review, fresh evidence).** The
  previous fix canonicalized discovered filenames via
  `discover_artifact_set()`, but discovery itself still used
  `glob("*.so")`, which never matches a bare `libfoo.so.1` in the first
  place. The example now discovers candidates via
  `abicheck.package.discover_shared_libraries()` — the same
  recursive, content-based ELF discovery the directory/package release CLI
  itself uses — before canonicalizing them.
