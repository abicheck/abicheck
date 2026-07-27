<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Pack manifests: `surface.internal_namespaces` is now order-insensitive
  too** (`compatibility_evaluation_packs.py`): the `contract.overlays`
  order-insensitivity fix from an earlier round missed
  `SurfaceConfig.internal_namespaces`, which `compatibility_evaluation_config.py`
  also canonicalizes (sorts + dedupes) — two contract packs assigning the
  same internal-namespace set in a different order raised the same
  spurious `PackConflictError` `contract.overlays` did before its fix.
  `_ORDER_INSENSITIVE_LIST_FIELDS` now covers both fields.

- **ADR-049 Phase 3 shadow evaluator: never-filter findings no longer
  downgraded by identity ambiguity** (no behavior change outside this
  still-unwired shadow module): `_NEVER_FILTER_KIND_NAMES` findings (leak
  findings, `constant_*` findings) are trusted unconditionally by
  construction, the same as `python_*` findings — but that trust only
  lived inside `_in_surface_result_is_confirmed`, reachable only after the
  identity-ambiguity gate already ran. A `VISIBILITY_LEAK` finding's
  `symbol="<visibility>"` sentinel (a batch finding with no real per-entity
  symbol) resolves to `finding_identity`'s reduced tier, so every
  visibility-leak finding was downgraded to `UNKNOWN_UNRESOLVED` before
  the never-filter trust ever applied. Moved the `_NEVER_FILTER_KIND_NAMES`
  check (alongside the existing `python_*` check) ahead of both the
  resolvable-surface gate and the identity-ambiguity gate.
