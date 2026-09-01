### Added

- **ADR-063 Phase 5's fact/capability registry: `AbiSnapshot.ast_resolved_standard`
  converted to `Fact[T]`** (schema v36) — the last remaining case-(b)
  field outside the four declaration dataclasses (`RecordType`,
  `EnumType`, `Variable`, `Function`), closing the case-(b) conversion
  scope this ADR's design section named entirely. Same "`None` already
  unambiguously means not captured" pattern as every prior case-(b)
  batch; `AbiSnapshot` gains its first `__post_init__` bridge for this.
