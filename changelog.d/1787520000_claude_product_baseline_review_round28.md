### Fixed

- **`abicheck.product_baseline.unpack_product_baseline`**: validated only
  that each manifest-declared library path existed as a file, never that
  its actual size/content matched the manifest's own recorded size/sha256
  — a stale or tampered archive whose extracted bytes no longer matched
  (e.g. a truncated library, or same-length corruption) was published
  unverified, and a later product comparison could silently analyze
  corrupted content instead of failing where the problem could be named.
  Now compares each library's actual size and SHA-256 against the
  manifest before publishing the staging directory.
- **`abicheck.product_baseline.compare_product_directories`**: an invalid
  `header_roots`/`old_header_roots`/`new_header_roots` root (absolute,
  escaping, or a bare-string typo) was only rejected inside the per-pair
  comparison loop — two directories producing zero matched library pairs
  (e.g. both empty, or entirely unmatched) never reached that loop, so
  the invalid root was silently accepted and the comparison could return
  `NO_CHANGE` instead of the documented `SnapshotError`. The same applied
  to a per-library mapping key naming a library that never ends up
  matched, which the per-pair lookup can never reach either way. The
  whole spec is now validated up front, independent of whether any pairs
  are found.
