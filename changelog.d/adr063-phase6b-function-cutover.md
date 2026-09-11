### Changed

- **Function old/new matching now reads through `SemanticIR`**
  (ADR-063 Phase 6B's third detector cohort, following typedefs and
  constants -- narrower in scope than either). The old/new *matching index*
  `diff_symbols._diff_functions` joins on (exact mangled name, plus the
  ambiguity-checked `extern "C"` name-alias fallback) is now built by
  `abicheck/compare/functions.py`'s `function_identity_index`, reading only
  through `SemanticIRIndex`/a new `legacy_function_ir` adapter projection
  -- never `AbiSnapshot.functions`/`.function_map` directly. The *resolved*
  identity for each function, and every richer per-parameter/return-type/
  cv/virtual-method/ctor-dtor/hidden-friend comparison, are unchanged: a
  function's identity has no legacy-vs-`SemanticIR` duality to migrate the
  way a typedef's or constant's did (its `entity_id` is resolved once, at
  parse time, by every producer, and copied — not recomputed — into
  `SemanticIR`), so this cutover is provably behavior-preserving rather
  than a bug fix. A new `semantic-ir-cutover` gate entry forbids
  `compare/functions.py` from reading `AbiSnapshot.functions`/
  `function_map` directly. No verdict, finding, or exit code changes.
