### Changed

- `cpu_dispatch_isa_dropped` follows one rule at every evidence level: the
  roll-up is graded `COMPATIBLE_WITH_RISK` only when headers were given and
  every removed member is proven export-only (declared in no public header).
  Without header evidence (a binary or DWARF-only comparison) nothing proves
  export-only, so the roll-up stays `BREAKING`; before, a DWARF-only
  comparison graded it a risk while a symbols-only one reported BREAKING. The
  modulation reason is now `member_not_proven_export_only`.
- The roll-up folds a member's removal in either spelling, so a symbols-only
  comparison no longer reports the grouped members again as
  `func_removed_elf_only` beside the roll-up.
