<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`stack_canary_removed`/`fortify_source_weakened` findings now carry
  `Change.evidence_provenance`** (`("both:l0:elf_symtab",)`), naming
  exactly which evidence tier produced them — the first real slice of
  G39's per-finding evidence-provider model (Phase 1). Both kinds are
  derived purely from `.dynsym` import/symbol names
  (`elf_metadata._finalize_hardening`), read identically on both sides of
  a comparison, with a single confirmed producer
  (`diff_platform_elf_dynamic._diff_security_hardening`). No user-visible
  behavior changes beyond the new, optional field on affected `Change`
  objects (JSON/SARIF/JUnit reports gain the field for these two kinds
  only; every other finding is unaffected). The valid provider-ID
  vocabulary now has a single code-level owner,
  `model.vocabulary.EVIDENCE_PROVENANCE_TAGS`, checked by a new
  completeness-gate test.
