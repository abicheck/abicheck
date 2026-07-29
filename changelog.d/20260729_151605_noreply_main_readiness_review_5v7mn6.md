### Fixed

- **The toolchain-identity check no longer silently no-ops on an
  unrecognized executable, and its `target` comparison is now
  compiler-family-aware.** A binding whose real compiler family couldn't
  be determined (unrecognized name and `--version` banner) previously
  passed any declared `compiler_family`/`target` unconditionally — that's
  now an error, since a hard validation gate that silently accepts
  whatever it can't identify defeats its own purpose. Separately, a
  declared `target` is now only compared against a **GCC-family**
  binding's probed `-dumpmachine` triple: a single Clang (or Intel
  oneAPI `icx`/`icpx`, which is clang-based) binary is inherently
  multi-target via its own `--target=` flag (which the profile's compose
  logic already passes explicitly and this module validates directly via
  a real invocation), so comparing its bare, host-default `-dumpmachine`
  output against a declared cross-compilation target previously rejected
  valid Clang cross-compiler profiles. A GCC-family binding with no probed triple at
  all is now an error too (an unverifiable claim), and the comparison
  additionally checks a coarse libc/environment marker (e.g. `musl` vs.
  `gnu`), so `target: x86_64-linux-gnu` bound to an Alpine `musl` GCC no
  longer passes just because architecture and OS matched.

