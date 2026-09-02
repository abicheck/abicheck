### Fixed

- **A `semantic_ir_conflicts` value is renumbered even when its own
  occurrence key is unchanged** — `renumber_conflict_keys()` previously
  skipped a conflict entry entirely once its occurrence's own identity
  carried no marker (so the key didn't need to move), even though the
  discarded value itself can independently name a different,
  marker-bearing type (e.g. a typedef aliasing a closure-parameterized
  template). The key and value are now checked, and rewritten, independently.
