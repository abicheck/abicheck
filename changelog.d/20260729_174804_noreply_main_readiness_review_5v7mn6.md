### Fixed

- **A `compiler_family: gcc`/`clang` profile bound to an auxiliary tool
  (not a compiler) is now rejected instead of silently accepted.**
  `_probe_compiler_family()` previously matched a resolved binding's
  basename via a bare substring check, so `gcc-ar`/`gcc-nm`/`gcc-ranlib`
  (GNU Binutils components installed alongside a GCC toolchain) and
  `clang-format`/`clang-tidy` (separate LLVM tools) — none of them
  compilers — were accepted for a declared `compiler_family: gcc`/`clang`
  purely because their basename contains "gcc"/"clang" as a substring.
  Compounding this, `gcc-ar`'s own `--version` banner carries the same
  "Free Software Foundation, Inc." copyright notice a real GCC does, so
  the banner-text fallback didn't rescue the case either. Name matching
  now requires an actual driver-alias spelling (optionally
  cross-compiler-triple-prefixed and/or version-suffixed), and the banner
  fallback is rejected whenever it also mentions "binutils".
