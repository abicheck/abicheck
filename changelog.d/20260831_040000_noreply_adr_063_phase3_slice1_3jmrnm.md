### Added

- **ADR-063 Phase 3 (D5), first two slices: `OccurrenceId`/`canonical_key`
  and `SurfaceGraphLike`.** `model.occurrence.OccurrenceId` is an
  `EntityId` plus an optional disambiguator for the one case a bare
  `EntityId` cannot resolve on its own -- two internal-linkage (`static`)
  declarations in different translation units sharing scope, leaf name,
  and signature (mangling carries no per-TU component). `canonical_key()`
  reduces to exactly `EntityId.key` whenever the disambiguator is empty,
  the common case. `model.graph_facts.SurfaceGraphLike` is a narrow,
  structural `Protocol` (read + write: `nodes`/`edges`/`has_node`/
  `add_node`/`add_edge`) that `SourceGraphSummary` already satisfies
  unchanged -- lets `model/snapshot.py`'s upcoming `surface_graph` field
  be typed with no `buildsource` import in `model`.
