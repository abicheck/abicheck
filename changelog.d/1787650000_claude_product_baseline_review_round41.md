### Fixed

- **`abicheck.product_baseline.pack_product_baseline`**: only a symlink
  *target* got Windows-path-semantics validation (drive-anchored, or a
  literal ``..`` under Windows separators) -- the archive member's own
  *name*, derived directly from a real on-disk relative path, did not.
  POSIX imposes no restriction on filename characters, so a file
  literally named ``C:library.dll`` or containing embedded backslashes
  like ``dir\..\outside.so`` packs unchanged and would fail (or be
  misread) on a Windows unpack via ``TarExtractor``'s own OS-native path
  validation, which rejects any ``..`` component outright regardless of
  whether it would actually escape the archive root. A new
  `_validate_portable_arcname()` check, applied to every archived file
  and directory member, closes the same gap for member names that the
  existing symlink-target validation already closes for targets.
