### Added

- **ADR-063 Phase 3 (D5), slice 13: closing regression coverage.** Three
  properties the phase's design calls for that weren't yet pinned by a
  direct test:
  - `tests/test_compare_public_entity_ids_threading_e2e.py` -- the full
    threaded path through `service.compare_snapshots(...,
    pattern_verdicts=True, surface_metrics=True)`, patching *two*
    independent boundaries in one run: `pattern_verdicts.py`/
    `diff_surface_metrics.py`'s own `old_public_entity_ids`/
    `new_public_entity_ids` pair, and `surface_graph.py`'s per-call
    singular `public_entity_ids` argument -- confirming the resolved ids
    genuinely reach both, not just one, and that the pair stays entirely
    unreached when both opt-in flags are off.
  - `TestPublicEntityIdsKindFilter` (`test_surface_graph.py`) -- a resolved
    `public_entity_ids` set legitimately mixes function/variable ids with
    record/enum/typedef ids (`PublicSurfaceQuery.resolve()`'s documented
    shape); `SurfaceGraph.public_roots()` must silently exclude the
    type-kind ids rather than mapping one onto a bogus root name.
  - `test_surface_graph_module_imports_nothing_from_policy` -- asserted
    directly against `surface_graph.py`'s own parsed imports, since the
    module is unclassified in `architecture/modules.yaml` and so the
    layer-boundary gate does not evaluate it at all.

  Every other regression the design's Phase 3 section calls for was
  already covered by an earlier slice's own tests (the shared-assembly
  identity test, the populated-graph round-trip, the pre-existing
  `surface_graph.py`/`pattern_verdicts.py`/`diff_surface_metrics.py` suites
  re-run unchanged, and the two-sided-correction test at both the
  `SurfaceGraph.public_roots()` and `apply_pattern_verdicts()` layers) --
  see each slice's own changelog fragment. The one item genuinely not
  applicable is the design's originally-envisioned "legacy snapshot lazy
  backfill" test: this implementation's `PublicSurfaceQuery` reads only
  already-resolved `entity_id` fields and delegates domain resolution to
  `surface.compute_public_surface()` unchanged (the documented Slices
  7/8 descope), so there is no lazy, graph-reading backfill path to pin.
