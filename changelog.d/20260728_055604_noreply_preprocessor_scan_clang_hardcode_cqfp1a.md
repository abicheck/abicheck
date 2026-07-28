### Fixed

- **`scan`'s S2 preprocessor pre-scan now honors `--gcc-path`** — it
  previously always shelled out to a hardcoded `clang++`, so a scan compiled
  with an Intel oneAPI/DPC++ toolchain (`--gcc-path icpx`, which accepts
  icx/icpx-only flags like `-no-intel-lib`) made every `clang -E` invocation
  in the pre-scan fail with `unknown argument`, degrading L3 preprocessor
  coverage (and, transitively, L4 source-ABI) to nothing. The pre-scan now
  resolves its own `clang -E`/`clang -M` binary from the scan's compile
  context the same way `--ast-frontend clang` does: a clang-family
  `--gcc-path` (icx/icpx/dpcpp/dpcpp-cl/clang/clang++) is used directly, a
  `--gcc-prefix` maps to the prefixed clang driver, otherwise it falls back
  to plain `clang++` as before.
