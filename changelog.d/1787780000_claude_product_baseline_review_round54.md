### Fixed

- **`abicheck.product_baseline.pack_product_baseline`**: the portable
  case-insensitive-collision check (guarding a Windows-unsafe archive)
  case-folded each path component without first normalizing Unicode --
  `str.casefold()` folds per codepoint without composing/decomposing, so
  an NFC and NFD spelling of the same visible name (e.g. `café.so`) are
  distinct byte sequences on a case-sensitive Linux host and folded to
  two *different* keys, even though APFS/HFS+ treat them as one
  filename and would silently overwrite one on extraction. Each prefix
  is now NFC-normalized before case-folding, closing the same collision
  class Unicode-equivalent-but-byte-distinct names create.
