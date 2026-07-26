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
  each selected pack's identity paired with its own resolved field-name ->
  value mapping — a policy pack's `ChangeKind`-slug -> `Verdict` overrides,
  or a contract/gate pack's own field assignments, are both just
  string-keyed mappings to this function, since D8's rule ("the same field
  *or* ChangeKind") is identical either way — and raises deterministically
  regardless of input order. However pack content is actually loaded is a
  front end's job this module doesn't yet own — packs currently only carry
  `ImmutableIdentity` references, with no content loader. A field already
  covered by an explicit override (e.g. `policy.overrides` for policy
  packs) is exempt, matching D8's composition order (`explicit override >
  selected packs > base policy`).
