<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **ADR-049 Phase 1: pack-conflict detection** (no behavior change):
  `abicheck/compatibility_evaluation_resolver.py` adds
  `detect_pack_conflicts()` and `PackConflictError`, implementing D8's "two
  selected packs that assign incompatible values to the same field or
  ChangeKind are a usage error until an explicit final override resolves
  the conflict. Pack order never decides semantics." The function takes
  each selected pack's identity paired with its own resolved
  `ChangeKind`-slug -> `Verdict` override mapping (however that content is
  loaded is a front end's job this module doesn't yet own — packs
  currently only carry `ImmutableIdentity` references, with no content
  loader) and raises deterministically regardless of input order. A
  `ChangeKind` already covered by an explicit `policy.overrides` entry is
  exempt, matching D8's composition order (`explicit override > selected
  packs > base policy`).
