<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **A closure/anonymous marker appearing only in a hybrid-merge conflict's
  discarded spelling is now renumbered too** — the preflight that
  `renumber_anonymous_closure_identities` uses to decide whether there's
  anything to renumber (and which ordinal each marker gets) used to scan
  only the retained `SemanticIR`/flat-field spellings, never
  `semantic_ir_conflicts`' own values. A marker present *exclusively* in a
  conflict's discarded value — the retained occurrence, its key, and every
  other field all marker-free — was invisible to it and stayed in raw
  `:line:col` form forever.
