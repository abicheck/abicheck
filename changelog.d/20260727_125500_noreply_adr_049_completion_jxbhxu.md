<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **ADR-049 Phase 1: `--policy-file`'s `internal_namespaces` list now
  resolves through the effective-config resolver** (`compatibility_evaluation_wiring.py`):
  a second real front-end wiring, `resolve_internal_namespaces`, alongside
  the existing `resolve_legacy_contract_mode`. `internal_namespaces` has no
  CLI flag of its own -- `policy_file.py`'s `PolicyFile.internal_namespaces`
  (populated only by a real `--policy-file` YAML) is the only front end
  that can set it today. An absent `--policy-file`, or one that sets an
  empty list (indistinguishable, once parsed, from never setting it),
  contributes no candidate and falls through to the built-in default
  (`()`), matching the same "a selector layer only participates when it
  actually selected something" principle the mode wiring already applies.
  The candidate value is sorted+deduped before resolution, mirroring
  `SurfaceConfig`'s own canonicalization of this order-insensitive field.
  Not called from any live command yet -- same non-authoritative status as
  the existing `contract.mode` wiring.
