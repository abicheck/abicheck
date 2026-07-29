### Fixed

- **Conflicting compiler-driver name and `--version` banner identity is now
  rejected instead of trusting the file's own name.** A resolved binding
  whose *file* is named like one compiler (e.g. `clang`) but whose real
  `--version` banner self-identifies as a genuinely different one (e.g.
  `gcc (GCC) 14.2.0`, a wrapper that execs a different compiler under the
  hood) previously passed a declared `compiler_family` unconditionally,
  since basename matching short-circuited before the contradicting banner
  was ever considered. Family detection now derives independently from
  the basename and the banner's own self-reported invocation name, and
  treats a disagreement between the two as indeterminate.
- **A profile declaring only `compiler_version` (no `compiler_family`/
  `target`) no longer accepts an arbitrary non-compiler executable.**
  Family detection was previously skipped entirely for a version-only
  overlay, so binding e.g. `compiler_version: ">=3"` to a real
  `/usr/bin/cmake` — not a compiler at all — passed unconditionally as
  long as its own `--version` banner happened to contain a matching
  dotted number. A version-only overlay now also requires the resolved
  binding to be a recognizable compiler.
