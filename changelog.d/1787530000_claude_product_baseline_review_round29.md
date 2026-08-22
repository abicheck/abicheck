### Fixed

- **`abicheck.binary_utils._canonical_library_key`**: a stored Mach-O
  snapshot transitioning between a versioned and unversioned dylib
  (`libfoo.1.dylib.abicheck.json` → `libfoo.dylib.abicheck.json`) never
  paired — the versioned side had its wrapper suffix dropped by the
  version-match branch, but the unversioned side (no numeric segment for
  that branch to match) fell through unchanged, keeping its wrapper
  suffix. Two different canonical keys for the same evolving library. Now
  scoped narrowly to a represented name genuinely ending in `.dylib`, so
  a name with no recognized binary extension at all keeps its prior,
  unaffected behavior.
- **`abicheck.product_baseline._is_library_path`**: a GNU ld
  `INPUT()`/`GROUP()` linker script (the conventional
  `libfoo.so -> INPUT(libfoo.so.1)` SDK-install pattern) is
  library-suffix-named but carries no binary content of its own.
  `_discover_library_map` already excluded it from comparison via its own
  separate check, but `pack_product_baseline`'s manifest-entry
  classification called this predicate directly with no equivalent
  exclusion, so the persisted manifest advertised the script as its own
  `LibraryEntry` — packing and comparison ended up with contradictory
  inventories for the identical tree. The exclusion now lives in the one
  shared predicate both paths call, matching the promise this function's
  own docstring already made ("Factored into one predicate so discovery
  and manifest classification can never drift apart on this question
  again"). The script itself is still archived as a regular file
  (round-tripping correctly) — just not double-counted as a library.
- **`abicheck.product_baseline.pack_product_baseline`**: `header_roots`
  accepted a bare string without rejecting it — `str` satisfies the
  declared `Sequence[str]` annotation, so the natural single-root spelling
  `header_roots="include"` (a typo for `["include"]`) iterated
  character-by-character instead of raising. The comparison side
  (`compare_product_directories`'s `_roots_for_library`) already rejects
  this same shape; the packing entry point now does too.
