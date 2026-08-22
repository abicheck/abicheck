### Fixed

- **`abicheck.binary_utils.resolve_linker_script`**: its GNU ld
  `INPUT()`/`GROUP()` text probe searched a file's first 8KiB with no
  check of its own for the absence of binary magic — a real ELF/PE/
  Mach-O binary whose content happened to contain the literal text
  `INPUT(`/`GROUP(`/`OUTPUT_FORMAT(` (embedded strings, symbol names, or
  plain coincidence) matched too, even though it was a genuine library,
  not a linker script. Via `abicheck.product_baseline._is_library_path`
  (the shared predicate round 29's linker-script exclusion moved this
  check into), a real library could be silently excluded from both
  packing and product comparison. Real binary magic bytes are now
  checked first — a genuine linker script has none — so this can only
  ever suppress a false positive, never mask a real one.
