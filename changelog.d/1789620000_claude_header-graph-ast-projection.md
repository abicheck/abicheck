### Performance

- **The header-graph attach no longer holds the parsed clang AST while it
  builds the graph.** `service._attach_header_graph` now reduces the AST to
  the four compact projections `build_header_only_graph` actually reads
  (`abicheck/buildsource/header_graph_ast_projection.py`) and releases the
  tree before allocating the graph, so the graph's long-lived objects land in
  arenas the parse has already freed instead of pinning them. Measured on an
  STL-bearing fixture: the attach's retained memory drops from 424 MiB to
  396 MiB (-7%), with wall time, every finding, the verdict and the exit code
  unchanged through the real `compare` CLI. This does **not** reduce the
  per-member *peak*, which is the JSON document plus the tree it is parsed
  into and is unchanged at 573 MiB — see
  `docs/contribute/measurements/header-graph-attach-memory.md` for the
  reattribution and what is left.

### Added

- **`scripts/check_header_graph_perf.py` now gates the header-graph attach's
  memory, not just its wall time** — `attach_peak_rss_mib` and
  `attach_retained_mib`, each measured in a fresh subprocess over an
  STL-bearing fixture and gated independently like the three existing time
  metrics (report schema `abicheck-header-graph-perf/3`). Holding an extra
  copy of the parsed AST costs no measurable time, so every pre-existing gate
  in that job passed a change of exactly this kind.
