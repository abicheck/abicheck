### Performance

- **The header-graph attach no longer holds the parsed clang AST while it
  builds the graph.** `service._attach_header_graph` now reduces the AST to
  the four compact projections `build_header_only_graph` actually reads
  (`abicheck/buildsource/header_graph_ast_projection.py`) and releases the
  tree before allocating the graph, so the graph's long-lived objects land in
  arenas the parse has already freed instead of pinning them. Measured on the
  real reference library (oneDAL 2024.7 `libonedal_core.so.2`, three fresh
  processes per side): the graph build's own residency cost falls from
  ~147 MiB to ~24 MiB, with wall time, every finding, the verdict and the
  exit code unchanged through the real `compare` CLI. Stated plainly because
  the obvious claim is wrong: it does **not** reduce the per-member *peak*
  (2216.3 → 2215.3 MiB, which is the JSON document plus the tree it is
  parsed into), and steady-state retention comes out ~8-12 MiB *higher*. See
  `docs/contribute/measurements/header-graph-attach-memory.md` for the
  reattribution and the measured ceiling of the one lever that remains.

### Added

- **`scripts/check_header_graph_perf.py` now gates the header-graph attach's
  memory, not just its wall time** — `attach_peak_rss_mib` and
  `attach_end_rss_mib` (absolute RSS, never a signed delta — `is_gateable`
  rejects `<= 0`, and a retained delta goes negative whenever the attach
  releases more than it allocates), each measured in a fresh subprocess over an
  STL-bearing fixture and gated independently like the three existing time
  metrics (report schema `abicheck-header-graph-perf/3`). Holding an extra
  copy of the parsed AST costs no measurable time, so every pre-existing gate
  in that job passed a change of exactly this kind.
