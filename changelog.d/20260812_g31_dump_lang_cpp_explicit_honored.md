### Fixed

- **`dump --lang c++`/`cpp` on the direct-clang backend is now honored on
  the primary snapshot pass, not just the header-graph pass** — an
  explicit `--lang c++`/`cpp` given to `abicheck dump --ast-frontend
  clang` used to be silently discarded on a syntactically C-compatible
  header (e.g. a plain POD struct), because Click's own `--lang` default
  is the identical string `"c++"`, so the primary snapshot pass could not
  tell an explicit request apart from the unspecified default and
  conservatively let auto-detection run instead — sometimes landing on C
  mode and silently dropping C++-only facts (`is_standard_layout`,
  `is_trivially_copyable`) even though `--lang c++` was passed. The
  header-graph pass reached the identical header via a different code
  path that *did* honor the raw value unconditionally, so the two passes
  could silently disagree about which language mode parsed the library's
  own headers. `dump` now resolves whether `--lang` was genuinely given on
  the command line (via Click's own parameter-source tracking) and
  threads that through so both the primary and header-graph clang passes
  agree on the same explicit-vs-auto-detected decision.
