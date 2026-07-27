<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049: LoadedPack.kind coercion and Phase 3 pipeline-exclusion
  handling** (no behavior change outside these still-unwired modules):
  `compatibility_evaluation_packs.LoadedPack.__post_init__` now coerces
  `kind` through `PackKind(...)` before branching on it -- a directly
  constructed `LoadedPack(kind="policy", ...)` (a bare `str`, not the enum
  member) failed the identity check that gates severity-value coercion,
  while `assignments_for_conflict_check()` still grouped it as a policy
  pack (equality/hash, not identity), so the two disagreed and
  `detect_pack_conflicts()` raised a false `PackConflictError`.
  `contract_evaluation.evaluate_change_contract_relevance()` now consults a
  finding's already-set `Change.surface_exclusion_reason` (set by
  `post_processing.py`'s `DemoteOffPythonSurface`/
  `DemoteUnreachableInternalChurn`) before recomputing membership from
  scratch, since a from-scratch `classify_change_surface()` call can
  disagree with the specialized detector that already excluded it.
