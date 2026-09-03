### Added

- **One Semantic Pipeline plan, "PR 2" (first slice)**: a new
  `model.semantic_ir_index.SemanticIRIndex` — a read-only query facade over
  an already-built `SemanticIR`, providing `entity(EntityId)`,
  `occurrences_for(EntityId)`, `entities_of_kind(EntityKind)` and the
  `functions()`/`variables()`/`records()` convenience filters, plus
  `fact(EntityId, fact_name)`. Pure, additive infrastructure: it re-derives
  nothing off `SemanticIR`/`SemanticIR.canonical_entities()`, and no
  detector reads through it yet — landed first, with its own
  primitive-level test suite, so the eventual `diff_symbols`/`diff_types`
  cutover onto `SemanticIR` (the plan's Phase 6B "semantic consumer
  cutover") has a concrete, already-tested read path to converge on. No
  behavior change.
