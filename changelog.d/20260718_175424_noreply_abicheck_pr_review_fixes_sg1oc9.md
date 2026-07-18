<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`collect-facts` Action's `producer: clang-plugin` now supports vendor
  Clang-based compilers such as Intel's `icpx`/`icx` oneAPI compilers** — the
  LLVM major is now detected from the compiler's own `__clang_major__` macro
  instead of being parsed out of `--version` (whose banner for `icpx`/`icx`
  reports a vendor product version, not an LLVM number, so detection
  previously failed outright). A new `llvm-cmake-prefix` input (auto-detected
  from `$CMPLR_ROOT` when unset) builds the plugin against that vendor's own
  bundled LLVM/Clang CMake package instead of an `apt-get`-installed
  `clang-<N>-dev`, which may not exist at all for a vendor's LLVM major.
