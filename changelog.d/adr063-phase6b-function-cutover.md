### Changed

- **Internal: groundwork for a future function-family `SemanticIR` cutover**
  (ADR-063 Phase 6B, following the typedef and constant cohorts). Adds
  `abicheck/model/semantic_ir_legacy_adapter.py`'s `legacy_function_ir` (a
  real `SemanticIR` projection of a snapshot's flat function map, tested
  directly) and a new `abicheck/compare/functions.py` module boundary for a
  future function-matching consumer. `diff_symbols._diff_functions`'s old/new
  matching now goes through `compare/functions.py`'s
  `function_identity_index`, which is currently a thin, behavior-preserving
  wrapper around the existing `SymbolIdentityIndex.for_functions` (no
  observable change to any finding, verdict, or exit code). The `functions`
  cohort is **not** registered in `scripts/semantic_ir_cutover.py`'s
  `MIGRATED_COHORTS`: an investigation found a function's identity has no
  legacy-vs-`SemanticIR` duality to migrate the way a typedef's or
  constant's did (`Function.entity_id` is resolved once, at parse time, and
  copied — not recomputed — into `SemanticIR`), so there is nothing for a
  matching-time `SemanticIR` lookup to meaningfully consume yet; registering
  the cohort anyway would have made the cutover gate pass without real
  `SemanticIR` dependence. See `abicheck/compare/functions.py`'s module
  docstring for the full account and what a genuine cohort 3 would still
  need.
