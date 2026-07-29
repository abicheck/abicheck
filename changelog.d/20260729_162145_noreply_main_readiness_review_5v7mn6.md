### Fixed

- **More GNU ABI-variant target suffixes no longer collapse into the
  same generic `"gnu"` environment.** `mips64el-linux-gnuabi64` (N64) vs.
  `mips64el-linux-gnuabin32` (N32), and `aarch64-linux-gnu` vs.
  `aarch64-linux-gnu_ilp32`, each contain `"gnu"` as a substring and
  previously matched the same generic marker, silently passing an
  incompatible MIPS/AArch64 data model in a declared `target:` (Codex
  review, fresh evidence — a third round beyond the earlier
  `gnueabi`/`gnueabihf`/`gnux32` fix).
