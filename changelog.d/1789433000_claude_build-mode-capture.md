### Fixed

- **An ELF dump now records the snapshot's `build_mode`** — the compiler
  family and version (from `DW_AT_producer`, falling back to the ELF
  `.comment` when debug info is stripped), the `DW_AT_language` standard
  bucket and the raw provenance strings are captured at dump time instead of
  always reading `UNKNOWN`; the stdlib-implementation detector prefers the
  recorded value and still falls back to mangled-symbol inference for stored
  baselines that carry none. Also fixes two detector defects this exposed:
  a GCC producer's version was read from a trailing flag digit
  (`-march=x86-64` → `4`), and `DW_LANG_C` (0x02) was bucketed as C++98
  while `DW_LANG_C_plus_plus` (0x04) and `DW_LANG_C_plus_plus_23` (0x3a)
  were unmapped.
