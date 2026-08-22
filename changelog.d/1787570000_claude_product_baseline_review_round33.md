### Fixed

- **`abicheck.product_baseline.unpack_product_baseline`**: the per-library
  checksum verification (rounds 28/30) only examined entries the
  manifest itself declared — a corrupt or adversarial archive that
  omitted a `LibraryEntry` entirely (or corrupted `libraries` to a
  non-list value `from_dict()` silently degrades to `()`) had that
  library's actual extracted content never verified at all, republishing
  tampered bytes under a manifest that falsely claimed the library
  didn't exist. The extracted tree is now cross-checked against the
  manifest using the same discovery walk `compare_product_directories`
  itself relies on, compared by filesystem identity (not path string, so
  a `pack_product_baseline`-produced archive's own dev-symlink aliases —
  which deliberately get no separate `LibraryEntry` — aren't flagged as
  false positives).
