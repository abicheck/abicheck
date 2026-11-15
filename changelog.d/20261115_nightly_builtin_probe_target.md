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
  Forced includes (`-include`, `-imacros`, and their joined and `--include=` forms)
  are dropped from the probe's options as well: a C++-only forced header would
  otherwise fail the C probe and discard every builtin fact. `-include-pch`
  is in that set, with its path operand.
- **A pre-74 snapshot no longer reports a false `VAR_ALIGNMENT_CHANGED` for an
  unchanged builtin.** Before generation 74 the clang builtin probe measured the
  host ABI, so an i386 `long` recorded 64 bits and a fresh i386 parse records 32.
  Across the generation-74 boundary only, that exact pair (the LP64 natural value
  against the ILP32 one, for `long`, `long double`, `size_t` and pointers) is
  annotated `COMPATIBLE_WITH_RISK` with the reason `extractor-generation drift;
  re-dump baseline`. A real alignment change is still reported.
