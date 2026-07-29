### Fixed

- **Compatible 32-bit ARM sub-architecture spellings no longer cause a
  false toolchain-target mismatch.** A GCC ARM cross-compiler's probed
  `-dumpmachine` output conventionally reports the generic `arm` (its
  actual instruction-set version is controlled by `-march=`/`-mcpu=`
  compile flags, not the triple), while a declared `target:` commonly
  spells the same real hard-float toolchain with an explicit Clang-style
  sub-architecture version (`armv7`/`armv7a`) — an exact architecture
  comparison previously rejected an otherwise-valid, OS/environment-
  matching ARM profile purely over this spelling difference.
  `_normalize_arch()` now folds `arm`/`armv6`/`armv7`/`armv7a`/etc. to one
  bucket, without touching the genuinely distinct 64-bit `arm64`/`aarch64`.
