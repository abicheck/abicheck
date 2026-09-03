### Changed

- **The typedef detector family now reads through `SemanticIRIndex`**
  (ADR-063 Phase 6B's first real checker cutover — previously no detector,
  verdict, or exit path read `SemanticIR` at all). Detection moved from
  `diff_types._diff_typedefs` into `abicheck/compare/typedefs.py`, which reads
  only through the index. A new legacy-flat-snapshot adapter
  (`abicheck/model/semantic_ir_legacy_adapter.py`) projects
  `AbiSnapshot.typedefs`/`typedefs_qualified`/`typedef_entity_ids` into a real
  `SemanticIR`, so the same detector runs unchanged over a DWARF/PE-only or
  pre-schema-v38 snapshot. Which backing is used is decided by a fidelity gate
  that requires the IR's own rendered display names to reproduce the
  comparison's alias maps exactly on both sides, so the cutover is
  behavior-preserving by construction rather than by assumption.

### Added

- **New `semantic-ir-cutover` AI-readiness check** (`scripts/semantic_ir_cutover.py`):
  a real AST scan forbidding a migrated detector cohort from reading back into
  the legacy `AbiSnapshot` collection it was migrated off, including via
  `getattr` and resolved `getattr` aliases. No allowlist — a freshly migrated
  cohort has nothing to grandfather.
