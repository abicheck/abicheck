### Changed

- **A newly linked versioned provider can now raise the binary's runtime floor.**
  A version node of a library the old binary did not need was reported as
  `SYMBOL_VERSION_REQUIRED_ADDED_COMPAT`, so a floor that rose from no requirement
  (for example `GLIBC_2.25` where the old binary needed no `libc` versions) was
  named only as the new dependency. Such a node above the binary-wide floor is now
  `SYMBOL_VERSION_REQUIRED_ADDED` and raises `RUNTIME_FLOOR_RAISED`; a node at or
  below it stays `COMPATIBLE`, as before, so the TBB 2021.13 `libdl` shape does
  not change. The verdict changes only for libraries whose newly linked provider
  raises the floor. Migration: re-check those builds, or suppress the floor finding
  if the raised requirement is acceptable (ADR-011 amendment, 2026-11-16).
