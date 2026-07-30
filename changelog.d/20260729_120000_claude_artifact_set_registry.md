<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY`** (ADR-056): a new
  audit-scoped bundle finding, produced by `scan --artifact-set`'s bundle
  audit (`abicheck.bundle._detect_unresolved_intra_dependency`). Unlike
  `bundle_intra_dep_removed`, this kind does not require an old side to
  diff against — it fires from a single-side resolution graph when a
  library in a declared artifact set imports a symbol that no library in
  the set exports and the import is not covered by the system-provider
  allow-list. Registered in `change_registry_composition.py`
  (`change_registry.py` is at the AI-readiness file-size hard cap), default
  verdict `COMPATIBLE_WITH_RISK`, evidence tier `L0`. See the companion
  fragment for `scan --artifact-set` and the GitHub Action wiring, and
  `docs/contribute/adr/056-*`/`docs/contribute/plans/g34-*` for the full
  design and what remains deferred.
