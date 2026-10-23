### Fixed

- A stated contract (`--contract public|exports`, or the legacy
  `scope: public: true`) no longer leaves binary-wide, build, toolchain and
  dependency-edge findings `UNKNOWN_UNRESOLVED`. Such a finding names nothing
  a contract can place, so it used to become an unsuppressible
  `finding_unresolved` coverage failure and exit 1. 59 kinds were affected,
  among them `toolchain_flag_drift`, `exceptions_mode_changed`,
  `rtti_mode_changed`, `stdlib_implementation_changed`, `dwarf_info_missing`,
  `target_dependency_added` and every `bundle_*` and `sycl_*` kind. They are
  now `NOT_APPLICABLE` in every contract mode and policy scores them as it
  does without a contract, so a strict preset still gates them.

### Changed

- Every `ChangeKind` catalog entry now declares a mandatory `contract_subject`
  (`declaration`, `dependency_edge`, `binary_wide`, `build_fact` or
  `analysis`; `ContractSubject` in
  `abicheck/model/change_catalog/dimensions.py`). Contract evaluation derives
  `NOT_APPLICABLE` from it and no longer keeps a hand-curated list, and
  `ChangeKindRegistry` rejects an entry that leaves it out (ADR-049's 2026-10
  amendment). `abicheck.policy.contract_closed_domain.SURFACE_METRIC_KIND_SLUGS`
  is removed; `ContractSubject` is also re-exported by `abicheck.change_registry`.
