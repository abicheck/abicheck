### Fixed

- **Regression tests for the v57 SemanticIR rollback** — the rollback that
  landed in #1490 (option (a) of the 6B closure: snapshot schema back to v56,
  `semantic_ir` document back to version 2, function/variable facts projected
  from the declaration store at comparison time and never persisted) is now
  pinned by tests: a snapshot loaded and then edited compares like a freshly
  built one, and a v57 snapshot, an IR document version 3, or an IR entity
  carrying a projection fact is refused.
