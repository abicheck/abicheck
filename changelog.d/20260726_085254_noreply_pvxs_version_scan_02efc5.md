### Fixed

- **Exported-object alignment: stdlib local-static template-instantiation
  symbols now exempt** — an Itanium `<local-name>`-production symbol
  (`_ZZ...`, a function-local `static` such as a libstdc++ `<regex>`
  template instantiation's lookup table) owned by the C++ runtime/standard
  library can never be named by any header declaration, so its
  address-derived alignment was a linker-placement artifact, not a declared
  ABI fact. `_check_object_alignment_reduced` (`diff_platform_elf_symbols.py`)
  now skips these via the new `is_stdlib_local_name_symbol()`
  (`name_classification.py`) the same way it already skips RTTI prefixes,
  closing a false-positive class found on a real pvxs `master` binary during
  a full-version-matrix validation scan (see
  `validation/pvxs-main-scan-2026-07-26.md`). Deliberately scoped to the
  runtime-owned subset, not every `_ZZ`-prefixed symbol: a library's own
  public inline/template function can own an ABI-visible
  (`STB_GNU_UNIQUE`-deduplicated) function-local `static` whose declared
  alignment consumers genuinely rely on.
