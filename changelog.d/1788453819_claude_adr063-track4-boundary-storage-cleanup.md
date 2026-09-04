### Changed

- **The composite Action's compare/scan verdict resolution now prefers the
  report's own `run_outcome` block (ADR-063 Phase 7 / D6)** —
  `_report_compat_verdict`/`_severity_gate_exit` in `action/run.sh` read
  `run_outcome.compatibility`/`run_outcome.gate` first, falling back to the
  pre-existing `verdict`/`severity.exit_code` fields (and, for a non-JSON
  report, the rendered text) only when no `run_outcome` block is present.
  Behavior-preserving for every report shape this Action already handles;
  ADR-063 Track 4 (7B)'s first landed slice.
- **A `"types"` storage section now has its own typed DTO** —
  `abicheck.storage.types_section_codec.TypesSection`, wired through
  `storage.dto.types_to_dto`/`types_from_dto`, replaces the generic
  `legacy_section_to_dto` pass-through envelope for the `"types"` D8 legacy
  section. ADR-063 Track 4 (8B)'s first typed-DTO promotion beyond
  `semantic_ir`; the on-disk section payload shape is unchanged.
