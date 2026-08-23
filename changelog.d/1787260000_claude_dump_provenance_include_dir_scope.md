### Fixed

- **This same PR's own defect-4/5 provenance-widening fix (see the
  `1787240000_claude_castxml_lambda_location_leak` fragment) had a real
  regression, caught by the example suite's `case184_internal_enum_churn_scoped`:
  a private implementation-detail header living in the *same directory* as
  its public umbrella header was silently promoted to `PUBLIC_HEADER`,
  defeating ADR-024's private-header scoping for that (very common) layout.**
  `dumper.dump()`'s `include_search_dirs` provenance widening was wired off
  `extra_includes` — the dump's *full* compile include path, which also
  carries a directory the dump auto-derives purely so an umbrella `-H`
  header's own relative `#include`s resolve (typically the umbrella
  header's own directory) — rather than off only the caller's genuinely
  *explicit* `-I`/`--include` list. `dump()` now takes a separate,
  caller-supplied `public_include_search_dirs` parameter for this; its two
  real production callers (`cli_dump_helpers.perform_elf_dump`,
  `service._dump_elf`) thread their own raw, pre-auto-derivation `includes`
  parameter into it instead of the combined value. The original defect-4/5
  promotion (a header reached transitively under a genuinely explicit `-I`
  root) is unaffected.
