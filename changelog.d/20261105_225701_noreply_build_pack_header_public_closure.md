### Fixed

- **A build pack no longer hides a public-to-internal dependency the headers show** —
  with `dump --build-info` (or an out-of-band `--build-info`/`--sources` pack at
  `compare`), the pack's L5 graph replaced the header graph and carried no (or
  only a partial) `SOURCE_DECLARES` public closure, so
  `public_api_internal_dependency_added` went unreported at build-source depth.
  `resolve_l5_source_graph` now unions the snapshot's header closure into the
  pack graph; dependency edges, pass flags and L5 coverage stay the pack's own.
  Only the declaration closure is borrowed: a dependency edge the header graph
  has but the pack lacks is not added, so build-source analysis can still miss
  a dependency the pack itself did not record.
