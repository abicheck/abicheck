### Fixed

- **Internal: closed the remaining `surface_graph` depth-exclusion gaps and
  fixed the root cause.** `internal_leak.py` and `buildsource/
  cross_source_checks.py`'s two checks were still using the unguarded
  `build_source.source_graph or surface_graph` fallback, so a `--depth
  build` comparison's retained, header-only `surface_graph` could still
  resurrect a leak-path or public-to-internal-dependency finding the
  excluded, richer L5 evidence would have produced. Separately,
  `policy/depth_projection.py`'s coverage-row bookkeeping only rewrote an
  L5 row that already existed, never inserting one for a pack that started
  with no coverage rows tracked at all — fixed at the source. All five
  migrated readers now share one `evidence_depth.resolve_l5_source_graph`
  resolver instead of five independently-maintained copies of the same
  fallback rule.
