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
  remaining Phase 1 work. `ContractConfig.unresolved` validates against
  ADR-049 D9's closed `{"not_checkable", "warn"}` vocabulary, and
  `EvidenceProviderRequirement.implementation` is required unconditionally
  (not only when `required=True`), matching D6's "every selected provider
  ... carries an immutable identity/version/digest" with no optional-provider
  carve-out. `GateConfig.exit_code_scheme` validates against
  `{"legacy", "severity"}` (ADR-037 D12's `"auto"` choice is a
  resolution-time input, already resolved by the time an effective
  `GateConfig` is constructed, so it's excluded here). `ValueProvenance`
  gains `shadowed_legacy`, populated by the resolver's `--policy`/
  `--policy-file` exception path to retain the suppressed legacy
  candidate's provenance for audit/replay (D7). `ImmutableIdentity.id`
  rejects the empty string, the same replay-exactness guarantee already
  applied to `sha256`. `CompatibilityPolicyConfig.overrides` now rejects
  unknown `ChangeKind` slugs (D8: a hard load error) regardless of which
  front end constructs it directly, matching `policy_file.py`'s
  `PolicyFile.load()` — not only the YAML-loading path.
