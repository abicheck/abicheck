### Fixed

- **Dependency scoping now drops a `semantic_ir_conflicts` entry alongside
  its excluded occurrence** — `scope_snapshot_excluding_dependencies()`
  already filters `semantic_ir.occurrences` to match the flat
  `types`/`enums` lists it filters; a hybrid-merge conflict recorded
  against one of those excluded occurrences now goes with it, instead of
  staying reachable in an otherwise "filtered" snapshot.
