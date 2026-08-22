### Fixed

- **`abicheck.product_baseline.unpack_product_baseline`**: round 28's
  checksum verification used `if lib.size and ...`/`if lib.sha256:`
  truthiness guards to tell a hand-edited/older manifest missing these
  fields apart from an explicit mismatch — but `pack_product_baseline`
  always writes a real size/sha256 for every `LibraryEntry`, so an
  attacker able to edit the manifest could trivially disable the whole
  check by zeroing/blanking the very fields it verifies, while still
  shipping tampered library content. Size is now compared unconditionally
  (never skipped for `0`), and `sha256` is validated as a genuine 64-hex
  digest before comparison — a missing or malformed digest is rejected
  outright rather than silently treated as nothing to verify.
