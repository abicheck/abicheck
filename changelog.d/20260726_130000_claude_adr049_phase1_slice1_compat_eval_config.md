<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **ADR-049 Phase 1 slice 1: `CompatibilityEvaluationConfig` typed object**
  (no behavior change): `abicheck/compatibility_evaluation_config.py` adds
  the frozen dataclass composition ADR-049 D7 specifies — `ContractConfig`,
  `EvidenceConfig`, `SurfaceConfig`, `AssuranceConfig`,
  `CompatibilityPolicyConfig`, `GateConfig`, `SuppressionConfig`, and
  field-level `ValueProvenance` — plus the `CompatibilityEvaluationConfig`
  that composes them. Reuses the existing `Verdict`
  (`change_registry_types.py`) and `SeverityConfig` (`severity.py`) types
  rather than duplicating them. This is the typed-object shape only — no
  resolver constructs one of these from real CLI/`.abicheck.yml`/recipe
  input yet; see `docs/contribute/plans/public-contract-default.md` for the
  remaining Phase 1 work.
