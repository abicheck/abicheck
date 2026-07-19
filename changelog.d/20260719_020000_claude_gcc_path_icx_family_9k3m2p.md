### Fixed

- **`--gcc-path` now recognizes Intel's `icx`/`icpx`/`dpcpp`/`dpcpp-cl`** as
  clang-family binaries for the `clang` `--ast-frontend`. Previously the check
  matched only a `"clang"` substring in the binary name, so a `--gcc-path`
  pointing at Intel's oneAPI DPC++/C++ compiler (clang-based but not
  clang-named) was silently ignored — the L2 header frontend fell back to a
  plain `clang` on `PATH` instead, parsing headers with a different,
  possibly-mismatched toolchain than the one the real build used.
