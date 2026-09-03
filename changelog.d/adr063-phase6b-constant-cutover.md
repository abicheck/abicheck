### Changed

- **The constant detector family now reads through `SemanticIR`**
  (ADR-063 Phase 6B's second detector cohort, following typedefs).
  `CONSTANT_ADDED`/`CONSTANT_CHANGED`/`CONSTANT_REMOVED` are now produced by
  `abicheck/compare/constants.py`, reading only through
  `SemanticIRIndex` -- with a legacy-flat-snapshot adapter
  (`abicheck/model/semantic_ir_legacy_adapter.py`) providing the identical
  read shape for a snapshot carrying no `SemanticIR`, so behavior is
  bit-for-bit unchanged. A new `semantic-ir-cutover` gate entry forbids
  `compare/constants.py` from reading `AbiSnapshot.constants`/
  `constant_entity_ids` directly. No verdict, finding, or exit code
  changes.
