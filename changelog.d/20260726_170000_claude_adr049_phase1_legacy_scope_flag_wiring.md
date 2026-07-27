<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **ADR-049 Phase 1: first real front-end wiring** (no behavior change,
  not called from any live command): `abicheck/compatibility_evaluation_wiring.py`
  adds `resolve_legacy_contract_mode()`, which resolves `contract.mode`
  from the real `--scope-public-headers`/`--no-scope-public-headers` CLI
  flag via `resolve_field()` and the D2 alias table
  (`LEGACY_SCOPE_FLAG_CONTRACT_MODE`) — the first ADR-049 module to
  construct a `FieldCandidate` from actual CLI-shaped input rather than
  hand-built test fixtures. The untouched-flag case falls through to a
  built-in default equal to today's real CLI default
  (`scope_public_headers=True` -> `public`), so accepting ADR-049 does not
  by itself change current default behavior. Wiring this into an
  authoritative code path is deferred to the Phase 3 shadow evaluator per
  `docs/contribute/plans/public-contract-default.md`'s rollout plan.
