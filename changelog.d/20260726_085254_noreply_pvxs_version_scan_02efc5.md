### Fixed

- **Exported-object alignment: local-static template-instantiation symbols
  now exempt** — an Itanium `<local-name>`-production symbol (`_ZZ...`, a
  function-local `static` such as a libstdc++ `<regex>` template
  instantiation's lookup table) can never be named by any header
  declaration, so its address-derived alignment was a linker-placement
  artifact, not a declared ABI fact. `_check_object_alignment_reduced`
  (`diff_platform_elf_symbols.py`) now skips these the same way it already
  skips RTTI prefixes, closing a false-positive class found on a real pvxs
  `master` binary during a full-version-matrix validation scan (see
  `validation/pvxs-main-scan-2026-07.md`).
