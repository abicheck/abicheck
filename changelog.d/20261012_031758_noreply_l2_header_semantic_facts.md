### Fixed

- **Headers-only graphs now carry template, override and callback facts** —
  a plain `abicheck dump lib.so -H api.h` (the default L2 header graph) used
  to keep only type and call edges, although the clang AST it parses holds
  template instantiations, virtual overrides and callback registrations. The
  header pass now derives those families from the same parse, through one
  owner shared with the L5 source pass (`buildsource/semantic_fact_folds.py`),
  so both produce the same facts; it stamps `header_override_graph`,
  `header_template_graph`, `header_callback_graph` and
  `header_virtual_dispatch_graph`. A graph that never looked for a family
  (one stored before this change) is reported in the structural graph diff's
  new `coverage_gaps` list instead of reading as removed or added facts. The
  streamed header reader and the L5 pass now read source locations the same
  way the whole-tree header parse does (made explicit), which also fixes a
  stream-vs-whole difference in declared-type files on STL-heavy headers.
  The header-graph projection cache moves to schema `/4`, so existing cache
  entries are re-derived once. No verdict or exit code changes.
