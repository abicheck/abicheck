### Added

- **One Semantic Pipeline plan, "PR 2" (preparation)**: a new real-fixture
  test suite, `tests/test_semantic_ir_index_function_parity.py`, proves —
  on an actually compiled library, for both header-AST backends — that
  `model.semantic_ir_index.SemanticIRIndex.functions()` sees exactly the
  same function identities as the legacy `AbiSnapshot.functions` list, and
  that two functions the current `Function.mangled`-keyed matching keeps
  apart never collapse onto one `EntityId`. This is the concrete fact a
  future `diff_symbols.py` cutover onto `SemanticIR`-based matching would
  need proven before the cutover, not discovered during it. Test-only —
  no production code path changes, no behavior change.
