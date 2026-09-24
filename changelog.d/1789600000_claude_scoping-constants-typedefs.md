### Fixed

- **Dependency scoping (and `--exclude-header`) now applies to constants and
  typedefs too.** Functions, variables, records and enums were dropped by
  their declaring header, but `constants`/`constant_entity_ids`/`typedefs`/
  `typedefs_qualified`/`typedef_entity_ids` passed through verbatim, so a
  constant declared only in an excluded header was still extracted and could
  *gate* a comparison (`DEP_CONSTANT: modified` → `Gate: REJECTED`) in the
  same run that warned the header's declarations were not observed. Both
  header backends now record each entry's declaring header (runtime-only;
  never serialized), and scoping drops a dependency constant outright and a
  dependency typedef unless the kept declarations name it (a public `size_t`
  parameter keeps `size_t`). The matching `semantic_ir` occurrences are
  dropped with them. **Transition note:** comparing a new dump against a
  baseline dumped before this fix lists the dropped dependency typedefs as
  `typedef_removed`; under default public-header scoping they are filtered
  as non-public and do not move the gate. Regenerate the baseline to clear
  them. Owner: `extract/flat_map_dependency_scope.py`.
