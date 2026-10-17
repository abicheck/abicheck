### Added

- **`EXPORT_LOSS_KINDS` names every finding that is evidence of a lost ELF
  export** — `func_removed`, `func_removed_elf_only`, `var_removed`,
  `var_removed_elf_only`, `func_deleted_elf_fallback`,
  `func_visibility_changed`, `var_visibility_changed`,
  `instantiation_missing_from_binary` and
  `internal_template_leaks_via_public_api` — in `policy/selectors.py`, next to
  the `binding:` selector that matches on their `symbol_binding`. An
  integration test builds a two-version library that loses WEAK inline,
  undeclared and template exports, and checks each such finding against the
  old ELF table, so a future emitter that drops the binding fails CI. Two
  example cases, `case217_export_loss_weak_binding_demoted` and
  `case218_export_loss_strong_binding_stays_breaking`, show a
  `binding: weak` reclassify rule demoting a WEAK loss to a risk while a
  GLOBAL loss stays breaking. No verdict or report field changes.
