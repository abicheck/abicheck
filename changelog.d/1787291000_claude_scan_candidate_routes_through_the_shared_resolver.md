### Changed

- **`scan`'s candidate resolution now goes through the same shared per-input
  resolver `compare`'s implicit dump and the typed `DumpRequest` API use,
  instead of a second, independently-maintained copy.**
  `scan_engine._build_new_snapshot` called `service.resolve_input` and
  `embed_build_source` directly and hand-wrote everything around them; it now
  builds an `InputSpec`/`SideEvidence` and calls
  `service_input_resolution._resolve_side_snapshot_impl`. The L2
  include/compile-context seed, the `parsed_with_build_context` stamp, the
  ADR-039 build-context collector's gate, the "drain the seed's cleanups
  before the embed step contends on the same inferred-build-dir lock"
  ordering, and the pair-aware "may the baseline reuse the candidate's folded
  context" rule are all inherited from one implementation rather than
  duplicated. Every one of those had already needed its own separate
  correction on the `scan` side, including one where `scan` turned out never
  to run the ADR-039 collector at all — which is exactly what a second copy
  produces.

  `scan`'s own behaviour is unchanged, and is preserved through opt-in
  parameters on the shared primitive rather than by changing it: its real
  collect mode for the L2 seed (so a compile-DB-less source tree still gets
  build-derived include seeding from the zero-config inferred query), its
  `--lang c` seed guard, its caller-owned cleanup list, its `"auto"` L4
  extractor, its expanded public-header roots, and its folded compile context
  as the L4 replay compiler selector. Verified by capturing the candidate
  snapshot, effective includes, effective compile context and deferred-cleanup
  count for three real build shapes at three collect modes before and after,
  and diffing: identical apart from wall-clock timestamps and the
  build-source pack's own content hash.

  One user-visible difference, on an error path only: an L3/L4/L5 collection
  failure during `scan` (a malformed pack, an unparseable `--config`) now
  reports through the same `Failed to load --binary <path>: …` wrapper the
  rest of that input's resolution already used, rather than surfacing the
  collector's message on its own. Same exit code, same underlying text, one
  added prefix naming the input being resolved.
