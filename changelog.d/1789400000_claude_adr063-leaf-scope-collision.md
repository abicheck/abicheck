### Fixed

- **The opaque-type by-value scan's leaf-spelling widening no longer
  collides with a real, separately-scoped reference.** `ns::Handle`'s
  unqualified leaf fallback `Handle` previously still matched a genuine
  `other::Handle` by-value reference (`::` is a non-word boundary on both
  sides, so plain token matching couldn't tell the two scopes apart),
  wrongly treating an unrelated declaration's exposure as exposing
  `ns::Handle` too — dropping a genuinely opaque type out of both identity
  tiers and reporting its private layout change as breaking. The leaf
  candidate now additionally refuses a match immediately preceded by `::`;
  the full, already-qualified candidate is unaffected (Codex review on
  PR #1041).
