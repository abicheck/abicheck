<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`relro_weakened`/`pie_disabled`/`writable_executable_segment`/
  `executable_stack`/`executable_stack_removed` findings now carry
  `Change.evidence_provenance`** — the second real slice of G39's
  per-finding evidence-provider model (Phase 1). Traced precisely against
  `elf_metadata`'s field-derivation code: `writable_executable_segment`/
  `executable_stack`/`executable_stack_removed` are pure ELF program-
  header/segment reads (`("both:l0:elf_program_headers",)`);
  `relro_weakened` combines a program-header check with a `.dynamic`
  flag (`("both:l0:elf_dynamic", "both:l0:elf_program_headers")`);
  `pie_disabled` combines a `.dynamic` flag with the ELF file header's
  own `e_type` (`("both:l0:elf_dynamic", "both:l0:elf_header")`). Two
  new provider-ID tags registered in `model.vocabulary.
  EVIDENCE_PROVENANCE_TAGS`: `both:l0:elf_program_headers` and
  `both:l0:elf_header`. No user-visible behavior change: the field is
  set only on the in-memory `Change` object today (report projection is
  G39 Phase 3).
