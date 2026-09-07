### Fixed

- **C/C++ language-mode auto-detection now consults the binary's own export
  table, closing a false-positive `exported_not_public`/
  `public_not_exported` pair on a self-comparison.** A public header with no
  structural C++ syntax at all (a plain top-level function or variable
  declaration — no `class`/`namespace`/`template`/`extern "C"`) gave the
  header-content auto-detection heuristic nothing to key on, so an
  unspecified `--lang`/`lang` silently parsed the header as C even for a
  genuinely compiled C++ library. That corrupted the declaration's mangled
  name, `is_extern_c`, and resolved `Visibility` alike, which made
  `crosscheck.py`'s `exported_not_public`/`public_not_exported` checks —
  wired into `compare`'s/`scan`'s automatic pipeline since the prior
  boundary-check migration — disagree with each other for the exact same,
  unchanged declaration. `dumper_toolchain._resolve_force_cpp` now also
  treats a real Itanium/Mach-O-Itanium/MSVC mangled export in the binary as
  decisive C++ evidence, checked only as a last-resort fallback behind an
  explicit `--lang` and any genuine C++ header syntax.
