### Fixed

- **`RecordType.qualified_name_fact` no longer misreports a confirmed
  global-scope determination as "not collected"** (ADR-063 Phase 5,
  follow-up to the fact/capability registry's second `RecordType` batch).
  Both header-AST backends now construct `qualified_name_fact` explicitly
  as `Fact.present(qualified_name)`, mirroring `is_final_fact`'s own
  convention: a `None` `qualified_name` on either backend is
  overwhelmingly a genuine "no enclosing scope" result, not missing
  evidence, so relying on the generic construction bridge's coarser
  none-means-omitted default understated the real evidence a fresh dump
  actually has.
