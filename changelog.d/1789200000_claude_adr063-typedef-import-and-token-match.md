### Fixed

- **`compare/typedefs.py` no longer imports `ChangeKind` through the policy
  layer.** It reached into `checker_policy.py` for its re-exported enum
  instead of importing directly from `ChangeKind`'s canonical owner,
  `model.change_catalog.kinds`, reversing the `compare -> model` dependency
  direction every sibling migrated comparison module already follows
  (Codex review on PR #1041).
- **`typedef_index_pair` moved from `model/` to `compare/`.** Choosing
  between two snapshots' competing `SemanticIR`/legacy representations is a
  comparison-orchestration decision, not a model shape or a single-snapshot
  projection, so it now lives in `compare/typedefs.py` alongside the
  typedef detector it selects an index pair for.
  `model/semantic_ir_legacy_adapter.py` keeps only the single-snapshot
  projection (`legacy_typedef_ir`) and the rendering/identity primitives
  both modules share (Codex review on PR #1041).
- **The opaque-type by-value scan's qualification-mismatch widening now
  matches a whole type-name token, never an embedded substring.** The leaf
  spelling `find_by_value_types` added for a qualified opaque type
  (`ns::Handle` -> also try `Handle`) previously matched a plain substring,
  so an unrelated `OtherHandle` by-value reference would wrongly count as
  exposing `ns::Handle`, dropping a genuinely opaque type out of both
  identity tiers and reporting a private layout change as breaking. The
  scan now requires a `\w`/non-`\w` boundary on both sides of every
  candidate spelling, including the pre-existing full-name candidate (which
  a real C/C++ identifier can never be a substring of another one against,
  so this changes nothing for a genuine reference) (Codex review on
  PR #1041).
