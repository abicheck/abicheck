<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049 Phase 3 shadow evaluator now evaluates demoted (out-of-surface)
  findings too, and reports expose their shadow decision**
  (`checker.py`/`reporter.py`; no behavior change outside the opt-in
  `contract_evaluation=True` path): `_apply_contract_evaluation_shadow`
  previously evaluated only `kept`, so a finding public-surface scoping had
  already demoted to `out_of_surface` never got a shadow decision at all --
  exactly the false-positive-reduction case Phase 3 exists to measure. It
  now evaluates `kept + out_of_surface` together; a demoted finding already
  carries its own `surface_exclusion_reason`, which
  `evaluate_change_contract_relevance` consults directly, resolving it to
  `PROVEN_OUT_OF_CONTRACT` without needing fresh surface evidence.
  `reporter.py`'s `_add_surface_scope` now serializes the same three
  `contract_relevance`/`contract_reason_code`/`contract_assurance` fields on
  `out_of_surface_changes` entries as it already does on ordinary `changes`
  entries (factored into a small shared helper so the two paths can't
  silently diverge again).

- **ADR-049 Phase 3 shadow evaluator: ELF loader-control state
  (`PT_INTERP`/`DT_BIND_NOW`/dlopen-dlclose flags/init-fini arrays) is now
  `NOT_APPLICABLE`** (`contract_evaluation.py`): `interpreter_changed`,
  `bind_now_disabled`, `dynamic_loading_flags_changed`, and
  `elf_init_fini_changed` are binary-wide loader-contract properties, the
  same synthetic-subject shape as the DT_NEEDED findings already covered,
  but were missing from the non-entity kind set.
