<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049: pack conflict-check projection scoped to namespace** (no
  behavior change outside this still-unwired helper):
  `compatibility_evaluation_packs.assignments_for_conflict_check()` now
  groups its projected `(identity, assignments)` pairs by `PackKind`
  instead of returning one flat list. A flat projection let a policy
  pack's `ChangeKind` slug (e.g. `func_removed`) and an unrelated
  contract/gate pack's own field name collide by string coincidence
  alone, raising a spurious cross-namespace `PackConflictError` even
  though D8 scopes conflict detection to packs within one namespace.
  Callers now run `detect_pack_conflicts()` once per returned kind group.
