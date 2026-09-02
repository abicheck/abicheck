### Fixed

- **A hybrid-merge conflict's own discarded-value text is now renumbered
  too, not just its key** — `renumber_conflict_keys()` moves a
  `semantic_ir_conflicts` entry to its freshly-recomputed key when the
  matching occurrence's closure/anonymous marker is renumbered, but left
  the stored value (the losing backend's own spelling) untouched even
  when it embedded the identical marker. It now goes through the same
  ordinal rewrite as every other reachable string.
