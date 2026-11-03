### Fixed

- **`concept_tightened` now requires proof of tightening** — the L4 replay
  reported any change to a C++20 concept's constraint fingerprint as
  `concept_tightened` (API_BREAK), so relaxing a concept, the exact reverse of
  case105, an equivalent rewrite (renamed requires-parameters, reordered
  requirements) and an unrelated rewrite all read as a source break. The Clang
  source extractor now records the constraint as a set of conjunctive
  requirements (requires-expression requirements and `&&` operands,
  alpha-normalized; anything else is one opaque atom), and the comparison
  reports `concept_tightened` only when the new set strictly contains the old
  one. Equal sets are no change; a relaxed, rewritten or undecomposable
  constraint — including any snapshot written before this encoding — is the
  new `concept_constraint_changed` (COMPATIBLE_WITH_RISK). The case105 README's
  L4 recipe, which used removed flags, is replaced with the current CLI.
