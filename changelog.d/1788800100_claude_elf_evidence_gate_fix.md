### Fixed

- **`elf`/`tls_checks`/`protected_visibility`/`symbol_version_alias`/
  `vtable_identity`/`abi_surface`/`elf_deleted_fallback` now report
  `not_evaluated` instead of a silent evaluated zero when a side has no ELF
  metadata.** These detectors used to substitute an empty `ElfMetadata()`
  for a missing side and compare two fabricated empty objects, which never
  produces a finding but recorded a real, evaluated comparison rather than
  the coverage gap it actually is (ADR-067 D3). No detector's emitted
  findings change on any input — only whether the gap is now recorded
  explicitly, alongside the pre-existing `pe`/`macho`/`dwarf` rows.
