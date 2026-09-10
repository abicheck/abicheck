### Fixed

- **`demangle()` no longer warns "C++ demangling unavailable" when a
  demangler is actually present.** The warning used to fire whenever a
  single symbol failed to demangle through both the `cxxfilt` package and
  the `c++filt` fallback — including when `c++filt` was genuinely installed
  and working, but this one symbol just wasn't real Itanium mangling
  (a malformed or foreign-ABI name c++filt legitimately echoes back
  unchanged). It now fires only when the `cxxfilt` package is confirmed
  unimportable *and* the `c++filt` binary is confirmed missing
  (`FileNotFoundError`); a present tool failing on one symbol is logged at
  debug level instead.
