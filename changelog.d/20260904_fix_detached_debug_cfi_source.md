### Fixed

- **DWARF advanced-channel CFI extraction now reads unwind data from the
  primary binary for detached debug info** — when `--debug-root`/
  `--debuginfod` resolves a standard `objcopy --only-keep-debug` sidecar,
  its own `.eh_frame`/`.debug_frame` are typically `SHT_NOBITS` (objcopy
  strips their content, keeping only the section headers), so CFI
  extraction from the sidecar alone failed or found no FDEs, stamping the
  advanced channel `partial` and making `--require-complete-analysis` fail
  for this repository's own supported detached-debug workflow. DIE-based
  analysis (structs, calling conventions, packed-typedef checks) still
  reads from the sidecar as before; only CFI extraction and its
  address-to-symbol correlation now read from the primary binary when one
  was resolved.
