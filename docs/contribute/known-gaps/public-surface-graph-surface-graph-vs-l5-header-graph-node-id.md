---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Public-surface graph: surface_graph vs L5 header_graph node-id namespaces not unified; type_reachability stdlib query unmigrated

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The traversal half is superseded: the closure walk moved to policy/public_surface_closure.py. Still true: abicheck/type_reachability.py is unmigrated, and the compare/surface_graph.py and header_graph node-id namespaces are not unified.

`policy/public_surface_index.py`'s
`PublicSurfaceQuery` delegates to `surface.compute_public_surface()`/
`export_surface.compute_export_surface()` unchanged rather than
reimplementing either as a literal graph traversal: both are exactly the
kind of intricate, multi-round-corrected logic this same page's
`_paired_stable_indices` incident (see "Primitive-level property tests"
in `AGENTS.md`) shows costs several review rounds to get right even once
already, and reimplementing one from scratch inside the same phase that
also had to build every piece of graph infrastructure underneath it was
judged materially higher-risk than landing the infrastructure now and
migrating the algorithm as its own later, narrowly-scoped phase.
Consequences: `compute_public_surface()`'s signature was never changed
to accept a structured `resolution` parameter, so there is no lazy,
graph-reading legacy-snapshot backfill path; `type_reachability.
directly_referenced_stdlib_types()` was not migrated into
`policy/public_surface_index.py` (doing so would reclassify `type_reachability.py`
into the `policy` layer, which would introduce a genuine new
`policy -> extract` architecture violation — that module imports two
already-`extract`-classified siblings); and `compare/surface_graph.py`'s
own node ids (`canonical_key(occurrence_id)`/`approx::`/`typedef::`
fallbacks) are a namespace fully independent of `buildsource/
header_graph.py`'s pre-existing L5 node ids (`decl://<identity>`/
`type://<identity>`) — one shared `SourceGraphSummary` instance carries
both builders' nodes (real, tested — `service_header_graph_attach.
_attach_header_graph()`), but the two schemes do not dedup onto a common
node for a declaration both builders see. Each of these is a real,
separate follow-up migration, not silently-abandoned scope — see the
implementation plan's Phase 3 "Landed"/D5 "Amendment" notes
(`docs/contribute/plans/one-semantic-pipeline.md`; ADR-063 itself no
longer carries a duplicated per-phase status block, removed by PR 0,
2026-09-02) and
`compare/surface_graph.py`'s/`policy/public_surface_index.py`'s own module
docstrings for the exact reasoning each carries.

**Correction (2026-09-01): the traversal-migration half of this entry's
own title is now stale — the algorithm was migrated after all, just not
in this phase's first landing.** A later round did reimplement
`surface.py`'s closure walk as a real traversal rather than leaving it
in place: `_index_surface_types`/`_seed_public_roots`/
`_walk_type_closure`/`_walk_exact_type_closure`/`_record_exact_identities`/
`_record_nested_in_known_record`/`_record_is_confirmed_public_seed` and
the `PublicSurface` type moved to `policy/public_surface_index.py` (dataclass +
indexing) and `policy/public_surface_closure.py` (the walk itself, plus
`resolve_public_surface()`), and `surface.py`'s own copies were
**deleted**, not kept alongside — `surface.compute_public_surface()` is
now a thin re-exporting wrapper. `export_surface.py`'s own root-seeding
stayed in place, but its final type-closure step now calls the same
migrated `_walk_type_closure` the header domain uses, so that domain
became graph-native for free. This is the risk the paragraph above
named and chose to defer, not a different fix — it just didn't stay
deferred through the whole phase. The next two bullets below give the
full, three-review-round account of what that migration actually needed
to get right (and what it does *not* touch — `snap.surface_graph`/
`GraphNode.attrs`, in the design that finally shipped). What is **still**
correctly described by the paragraph above, unchanged: `export_surface.py`'s
own root-seeding logic, `type_reachability.directly_referenced_stdlib_types()`
staying unmigrated (same `policy -> extract` reason), and the two node-id
namespaces not deduping onto one node. See the implementation plan's
Phase 3 "Landed"/D5 "Amendment" notes for Phase 3's final accounting —
ADR-063 itself no longer carries this per-phase detail.
