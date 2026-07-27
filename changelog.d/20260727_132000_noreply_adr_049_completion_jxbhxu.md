<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049 Phase 3 shadow evaluator: remaining ELF/Mach-O loader-state
  findings are now `NOT_APPLICABLE`** (`contract_evaluation.py`; no
  behavior change outside this still-unwired shadow module):
  `symbolic_binding_mode_changed` (DT_SYMBOLIC/DF_SYMBOLIC toggle) is the
  same synthetic-subject loader-control shape as the PT_INTERP/DT_*
  findings already covered. Separately, `compat_version_changed`
  (Mach-O's LC_ID_DYLIB compat_version) is a binary-wide loader-contract
  property with its own synthetic `symbol="compat_version"` subject, the
  Mach-O counterpart of the ELF loader-state findings above. Both were
  missing from the non-entity kind set.
