### Fixed

- **`scan --against` now collects the same ADR-039 build-context evidence a
  `dump` baseline already carried.** `scan`'s candidate resolution was the one
  of the three input resolvers that never ran the ADR-039 collector at all, so
  a candidate given `--build-info` naming a real compile database carried no
  `build_context_defines`/`conditional_fields` while the `dump`-produced
  baseline it was compared against carried both — and the reconciler could not
  clear a context-free header-parse false positive (a `#ifdef`-guarded record
  field the context-free parse pruned) on the candidate side the way it already
  did on the baseline's. The gate every resolver applies now lives in one
  shared function, `header_conditionals.attach_build_context_for_parsed_headers`,
  so the ELF `dump` CLI, the typed `compare`/`dump` pipeline, and `scan` cannot
  drift again on what "this input has build context" means. CLI cleanup phase
  two, PR 3A.

### Changed

- **The typed input resolver releases an L2-seeded inferred build directory
  before its own L3–L5 embed step, not after it.**
  `service_input_resolution._resolve_side_snapshot_impl` drained the seed's
  cleanups only at the end of the function, so an inferred build query's
  exclusive lock was still held when the embed step ran its own inferred query
  — the same in-process self-contention (up to a 600s timeout) that
  `scan`'s resolver already avoids. Latent rather than live for today's callers
  (the seed's collect mode is pinned "off", so no caller can run an inferred
  query there yet), and fixed now so it is not a trap for the CLI resolvers
  still to be migrated.
