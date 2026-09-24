### Fixed

- **castxml now parses C++20 headers under the standard you asked for.**
  castxml learns its predefined macros and system include paths by running
  the compiler it emulates (`--castxml-cc-gnu g++`), and it passes that
  compiler only the arguments inside the emulation group. abicheck put
  `-std=`, `--sysroot`, `-nostdinc`, `-m*` target flags and feature-macro
  `-f` flags only after the group, so with `compile.std: c++20` g++ still
  reported `__cplusplus 201703L`. libstdc++ then declared no
  `std::integral`, and any C++20 header using `<concepts>` failed to parse
  (Intel SVS failed with 20 errors). Both castxml command builders (L2
  header dump and L4 source replay) now also pass those flags to the
  emulated compiler, through one shared rule in
  `abicheck/extract/castxml_compiler_emulation.py`; Clang-only spellings
  are passed only when the emulated compiler is Clang. Cached castxml
  parses and snapshots from earlier versions are invalidated.
- **castxml dumps no longer fail or leak on function-local declarations.**
  castxml emits a declaration local to a function body when a signature
  refers to it, such as a local alias or class used as a deduced `auto`
  return type. Such a local typedef made abicheck refuse the whole dump
  (the typedef identity table and `SemanticIR` disagreed); libstdc++'s
  C++20 `std::string` `operator<=>` triggers it. A local class or enum was
  recorded under a namespace-level identity (`q::V` for `q::mk()::V`),
  together with its implicit members. The castxml parser now leaves every
  function-local declaration out of the snapshot, matching the clang
  backend.
