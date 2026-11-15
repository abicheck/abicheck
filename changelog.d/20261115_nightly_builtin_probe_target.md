### Fixed

- **Clang header parses under `-m32` now measure the 32-bit builtins.** The
  builtin-ABI probe received only the parse's `gcc_option_tokens` and dropped
  the `gcc_options` string, so `-m32` never reached it and `long`, `time_t`
  and the other LP64 builtins took x86-64 sizes. The probe now receives the
  same options the parse runs with (`parse_option_tokens`). The probe also no
  longer fails on i386, where `__int128` is rejected and one rejected spelling
  discarded every builtin: the 128-bit spellings are guarded by
  `__SIZEOF_INT128__`.
  The probe also drops `-std=` / `--std=` from its options, since it compiles C
  and a C++ standard would discard every builtin fact of a C++ parse.
