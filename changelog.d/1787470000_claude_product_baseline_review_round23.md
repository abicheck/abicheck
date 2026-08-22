### Fixed

- **`abicheck.binary_utils._canonical_library_key`**: preserves the
  matched extension's own case when stripping a Mach-O version segment
  (`libfoo.1.DYLIB` → `libfoo.DYLIB`), instead of always substituting a
  fixed lowercase `.dylib`. The fixed-case substitution normalized a
  versioned uppercase extension to lowercase while leaving an unversioned
  uppercase extension untouched, so a real version-drop pair
  (`libfoo.1.DYLIB` → `libfoo.DYLIB`) landed on two different-cased
  canonical keys and was never paired by the canonical fallback.
