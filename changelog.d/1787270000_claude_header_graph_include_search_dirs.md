### Fixed

- **The L5 header-only semantic graph could disagree with the flat snapshot
  on a declaration's own public/private classification.** Once a header
  reached transitively under a caller's explicit `-I` root is promoted to
  `PUBLIC_HEADER` (the defect-4/5 fix, `include_search_dirs` in
  `provenance.apply_provenance`), the same widening now also reaches
  `buildsource.header_graph.build_header_only_graph()` — its own
  `header_node()` previously reclassified that header's graph node fresh
  from only the bare `public_header_paths`/`public_dir_paths`, so a type
  could read `public_header` for its own declaration but `private_header`
  for its own defining header node, risking a false public-to-internal
  dependency finding. `build_header_only_graph` takes a new
  `include_search_dirs` parameter (mirroring `apply_provenance`'s own),
  threaded from `service._attach_header_graph`'s identical new parameter,
  fed by each caller's own raw, explicit `-I` list — the same value already
  used for the flat-snapshot fix.
