### Performance

- **The header-graph attach no longer re-parses the clang AST on a warm
  run.** It caches the projection `build_header_only_graph` actually reads
  (`abicheck/buildsource/header_graph_projection_cache.py`) beside the AST
  cache entry it was derived from, so a repeat run loads a small document
  instead of rebuilding a gigabyte of dicts to recompute the same answer.
  Measured on oneDAL 2024.7 `libonedal_core.so.2`, fresh process per run:
  peak RSS **2093.5 → 434.7 MiB (−79%)** and attach wall time **22.4 → 6.4 s
  (−71%)**, with a bit-identical graph (49481 nodes / 98330 edges, same
  digest). The cached projection is 16.5 MiB against an 822.4 MiB AST entry
  — 50x smaller. A cold run is unchanged: it still parses once, and now
  stores what it computed.

  The cache is derived, never authoritative. It is named after the AST entry
  rather than keyed by a second, independently derived key, so it invalidates
  exactly when that entry does; its schema is described by the edge
  dataclasses' own field lists, so adding or reordering a field invalidates
  it automatically instead of relying on someone to bump a version; and any
  entry this build cannot read is discarded and re-parsed. Every failure mode
  costs one re-parse, which is what the run would have done anyway.
