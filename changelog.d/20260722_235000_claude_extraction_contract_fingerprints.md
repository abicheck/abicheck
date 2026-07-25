<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`AbiSnapshot.contract` — profile/scope extraction-contract fingerprints**
  (ADR-050 D1, G32 Phase A slice 1): a new `abicheck.comparability` module
  computes a `profile_fingerprint` (resolved compiler/macro/`-I`-content
  identity) and `scope_fingerprint` (declared header/TU identity, root-
  relative so the ordinary two-checkout `compare` workflow is unaffected)
  for a snapshot's extraction, plus `check_contracts_comparable(old, new)`
  raising `ProfileMismatchError`/`ScopeMismatchError` when two snapshots'
  contracts genuinely disagree — including a carve-out so a target-
  architecture-only mismatch defers to `diff_platform.py`'s existing,
  more specific `elf_machine_changed`/etc. detectors instead of masking
  them. Snapshot schema bumped 11 → 12 for the new `contract` field; a
  reader predating schema 12 now hard-rejects a v12+ snapshot
  (`IncompatibleSnapshotSchemaError`, a `SnapshotError` subclass) instead of
  silently warning past an unrecognized, verdict-blocking field.
  **Not yet wired in**: `dumper.py` does not populate `contract` on a real
  dump yet, and `check_contracts_comparable` is not yet called from
  `checker.compare` or any CLI/API entry point — every snapshot produced
  today still has `contract=None`, so this change has no behavioral effect
  until a follow-up PR wires it in. See
  `docs/development/plans/g32-comparability-contract-and-multi-tu-manifest.md`
  Phase A for the full remaining scope.
