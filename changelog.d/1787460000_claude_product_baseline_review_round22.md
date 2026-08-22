### Fixed

- **`abicheck.product_baseline.compare_product_directories`**: a
  structurally invalid `header_roots`/`old_header_roots`/`new_header_roots`
  entry (absolute, or escaping the product directory) was silently dropped
  instead of rejected — the per-library compare still ran, just with that
  side's header evidence quietly missing, risking a false-green result for
  an API/header-only break the header evidence would have caught. Now
  raises `SnapshotError`, matching the containment discipline pack/unpack
  already enforce for a manifest-declared path. A well-formed root that
  simply doesn't exist for a given library is still tolerated (that
  library legitimately ships no public headers there).
- **`abicheck.product_baseline.compare_product_directories`**: the
  standalone-removal/addition fallback's `already_reported` exclusion
  check matched by bare filename alone, so when `compare_bundle()`
  reported a removal/addition for a basename two distinct unmatched
  libraries shared (`plugins/a/plugin.so`, `plugins/b/plugin.so`), *both*
  were suppressed from the fallback — including whichever one
  `compare_bundle()`'s own bare-filename collapse discarded before it was
  ever analyzed, silently losing a real, distinct removal/addition.
  Now only excludes a key when it's actually the one that survived the
  collapse (i.e. the one `compare_bundle()` could have reported on).
- **`abicheck.binary_utils._canonical_library_key`**: case-folding for PE
  identity now detects a PE image by content
  (`_pe_is_dll_content`, moved here from `product_baseline.py`), not just
  the `.dll` suffix — a case-only rename of a PE library shipped under a
  nonstandard extension (a Python `.pyd` extension module) is just as
  case-insensitive on Windows as a `.dll`'s, and the suffix-only check
  previously missed it.
