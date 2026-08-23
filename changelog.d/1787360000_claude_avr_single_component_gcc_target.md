### Fixed

- **The GCC-triple validation added earlier in this PR rejected a real
  relocatable GCC install targeting a single-component embedded/bare-metal
  machine (e.g. AVR: `lib/gcc/avr/12.2.0/include`).** `_TARGET_TRIPLE_RE`
  requires at least one hyphen, but several real GCC targets (AVR,
  MSP430, and other bare-metal architectures) have no vendor/OS/
  environment components at all — just a bare name. Those compiler-owned
  headers then survived default dependency exclusion, or became public
  when supplied via an explicit `-I`, risking false ABI findings from the
  toolchain surface. Fixed by additionally accepting a known,
  non-exhaustive set of real single-component GCC target names at this
  position — the version directory's own strict digits-only check is what
  keeps an arbitrary, unrecognized single-word project directory rejected.
