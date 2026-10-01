### Changed

- ADR-063 Phase 3/10: a header-only dump no longer carries a synthesized
  `build_source` pack whose only content was the header graph. The graph
  lives on `AbiSnapshot.surface_graph` alone, and every reader (L5 evidence
  resolution, coverage rows and presence, depth labels, the `--build-info`
  embed backfill, `buildsource merge`, consumer-impact explanations,
  reachability trust) derives the same answers from it. Reports are
  unchanged. Newly written header-only snapshots omit the `build_source`
  key, and older documents that carry it still load as before. A depth
  projection below `source` now also drops `surface_graph` when no pack
  survives, so L5 cannot reappear for a comparison that excluded it.
