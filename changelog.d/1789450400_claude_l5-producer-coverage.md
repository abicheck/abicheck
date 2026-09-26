### Fixed

- **L5 source-graph findings no longer treat an edge nobody produced as an
  absent edge.** The five L5 producers that stamped no coverage -- the L4
  source-ABI fold (`SOURCE_DECLARES`, `SOURCE_DECL_MAPS_TO_SYMBOL`, plus a
  header-only `header_declarations` counterpart), the build-target fold
  (`TARGET_HAS_PUBLIC_HEADER`, `TARGET_DEPENDS_ON`) and the build-option linker
  (`BUILD_OPTION_AFFECTS_SYMBOL`) -- now record a pass
  (`source_abi`/`build_targets`/`build_options` in
  `model/source_graph_coverage.py`; `source_abi` is `degraded` when the L4
  replay reports a declaration family failed or partial). The mapping-drift,
  public-reachability, generated-closure, build-option and target-dependency
  findings fire only when the side lacking the edge proved its absence
  (`compare.edge_query.source_graph_covers`). The legacy edge-presence reading
  is retired for graphs that record pass flags: an unflagged graph (hand-built,
  or an L5 graph stored before this change) answers every absence `unknown`,
  shown in the report's "Relationship coverage" section, and its
  `coverage.*.collected` summary states `pass_flags_recorded: false`. As a
  consequence, comparing against a stored baseline whose L5 graph predates
  this change reports none of those five families until the baseline is
  regenerated. No schema change.
