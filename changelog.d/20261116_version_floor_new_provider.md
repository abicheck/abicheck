### Changed

- **A newly linked versioned provider can raise the binary's runtime floor.**
  A version node of a library the old binary did not need was reported as
  `SYMBOL_VERSION_REQUIRED_ADDED_COMPAT`, so a floor that rose from no requirement
  (for example `GLIBC_2.25` where the old binary needed no `libc` versions) was
  named only as the new dependency. A node above the architecture's `GLIBC_`
  baseline (x86-64 `GLIBC_2.2.5`, i386 `GLIBC_2.0`) is now `SYMBOL_VERSION_REQUIRED_ADDED`
  and raises `RUNTIME_FLOOR_RAISED` from that baseline. A node at or below the
  baseline stays `COMPATIBLE`, so a newly linked `libc` baseline version and the
  TBB 2021.13 `libdl` shape raise nothing. Architectures without a measured baseline
  get no exemption. The verdict changes only for libraries whose newly linked
  provider raises the floor above the baseline. Migration: re-check those builds, or
  suppress the floor finding if the raised requirement is acceptable (ADR-011
  amendment, 2026-11-16).
