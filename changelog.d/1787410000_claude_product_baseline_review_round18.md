### Fixed

- **`abicheck.product_baseline.pack_product_baseline`**: an extensionless
  ELF shared library now gets a `LibraryEntry` in the manifest. Manifest
  classification previously used a filename-only check while discovery had
  already gained a content-aware ELF fallback, so such a library was
  archived but the returned/persisted manifest falsely reported the
  product had no such library. Discovery, packing, and manifest
  classification now share one predicate.
- **`abicheck.product_baseline._discover_library_map`**: a GNU ld
  `INPUT()`/`GROUP()` linker script (the conventional
  `libfoo.so -> INPUT(libfoo.so.1)` SDK-install pattern) is no longer
  discovered as a library in its own right. Previously it was compared
  twice — once directly, once via `run_compare()` following it to its real
  target — producing duplicate `DiffResult`s, and an unresolvable script
  (no real target present) could abort the whole comparison outright.
- **`abicheck.product_baseline.pack_product_baseline`**: the output
  scaffold's cleanup now also covers a `tempfile.mkstemp()` failure
  (`ENOSPC`, `EMFILE`, a permission race) — previously the call sat between
  two existing cleanup `try`/`except` blocks, uncovered by either.
- **`abicheck.product_baseline.unpack_product_baseline`**: each manifest
  library entry's declared path is now validated the same way header roots
  already are — rejecting an absolute or escaping path, or one naming
  nothing that exists — before publication. Previously a corrupt or
  adversarial manifest's `LibraryEntry.path` was returned unchanged, so a
  caller resolving it against the unpack destination could be pointed
  outside the unpacked baseline entirely.
- **`abicheck.product_baseline.compare_product_directories`**: a non-ELF
  library (`.dll`/`.dylib`) added in the new product is now reported as a
  `bundle_library_added` finding, symmetric to the existing standalone-
  removal fallback. `compare_bundle()`'s own addition detection reads
  library names off its ELF-only `BundleSnapshot`, so a newly added
  Windows/macOS library previously reached neither snapshot and the
  comparison silently returned `NO_CHANGE`.

### Changed

- Split `tests/test_product_baseline.py`'s `compare_product_directories`
  coverage into a new sibling file, `tests/test_product_baseline_compare.py`
  (AI-readiness file-size cap) — the original file kept growing with each
  review round and crossed the 2000-line hard limit. No test behavior
  changed; same fixtures, same assertions, just split by concern (pack/
  unpack archive format vs. the comparison entry point).
