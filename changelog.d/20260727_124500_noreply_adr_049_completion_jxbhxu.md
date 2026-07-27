<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049 Phase 3 shadow evaluator: ELF machine/ABI-flag drift and
  DT_NEEDED reordering are now `NOT_APPLICABLE`** (`contract_evaluation.py`;
  no behavior change outside this still-unwired shadow module):
  `elf_machine_changed` (e_machine drift) and `elf_abi_flags_changed`
  (decoded float-ABI/EABI drift) are binary-wide architecture/
  calling-convention identity -- the ELF analogue of
  `pe_machine_changed`/`macho_cpu_type_changed`, already covered -- but were
  missing from the non-entity kind set. Separately, `needed_order_changed`
  (a pure DT_NEEDED reorder with the dependency set unchanged) is the same
  loader-level state as `needed_added`/`needed_removed`, not a different
  kind of entity, and was missing too. All three now short-circuit to
  `NOT_APPLICABLE` instead of falling through to header-surface
  classification.
