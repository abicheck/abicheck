### Fixed

- **Intel's oneAPI DPC++/C++ compiler (`icx`/`icpx`) is now recognized as
  its own toolchain family instead of being indeterminate.** A declared
  `compiler_family: icx` profile was previously rejected unconditionally,
  since neither the `icx`/`icpx` driver name nor its `--version` banner
  matched the existing GCC/Clang/MSVC detection — even though
  `CompilerFamily.ICX` is already a recognized, distinct family elsewhere
  in the codebase. `icx`/`icpx`/`dpcpp`/`dpcpp-cl` binary names and the
  `"oneAPI"` banner signature now resolve to `"icx"`, and (since it's a
  clang-based driver accepting the same `--target=` flag) its `target:`
  check follows the same multi-target-exempt, real-probe-validated path
  Clang already uses.
- **A real Intel `--version` banner no longer has its build identifier
  parsed as the compiler version.** `Intel(R) oneAPI DPC++/C++ Compiler
  2026.1.0 (2026.1.0.20260617)` has no `"version"` keyword and puts its
  real version *before* a parenthesized build identifier — the opposite
  arrangement from GCC's package descriptor (which comes *before* the real
  version) — so the prior last-dotted-token heuristic picked
  `2026.1.0.20260617` over the real `2026.1.0`, rejecting valid `==`/`<=`
  constraints. `_extract_version_token` now excludes any dotted match that
  falls inside a `(...)` span before picking the last remaining candidate,
  handling both package-descriptor arrangements uniformly.
