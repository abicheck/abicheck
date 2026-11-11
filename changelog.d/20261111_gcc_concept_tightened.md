### Fixed

- **native GCC header backend: constraint direction** — the gcc header
  backend records a template's or concept's C++20 constraint as the shared
  conjunctive atom set (one atom per top-level `&&` conjunct, the empty set
  for an unconstrained template) instead of one opaque digest. A requirement
  added to a template is now `concept_tightened` (API break) as with the
  clang backend, not `concept_constraint_changed`. `EXTRACTOR_GENERATION` 61,
  snapshot cache salt v55.
