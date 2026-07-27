<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049 Phase 3 shadow evaluator: Mach-O CPU architecture changes are
  now `NOT_APPLICABLE`, and added overloads now use new-side authority**
  (`contract_evaluation.py`; no behavior change outside this still-unwired
  shadow module): `macho_cpu_type_changed` -- the Mach-O analogue of
  `pe_machine_changed`/`elf_class_changed` -- was missing from the
  non-entity kind set, so it fell through to header-surface classification
  instead of the `NOT_APPLICABLE` its PE/ELF siblings already received.
  Separately, `overload_added` (a new overload under an existing public
  name) is the same "a new declaration appears" shape as `type_field_added`/
  `virtual_method_added` -- an added overload's old-side header evidence is
  unresolvable by construction (the overload didn't exist yet), so it now
  uses new-side authority too, renaming
  `_BREAKING_ADDITION_SHAPE_KINDS` to `_NON_COMPATIBLE_ADDITION_SHAPE_KINDS`
  to reflect the broader membership.
