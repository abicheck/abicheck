### Changed

- **ADR-063 Phase 3 (D5): the public-surface closure walk is now a real
  graph traversal, not a delegation.** `policy.public_surface_query.
  PublicSurfaceQuery`'s public-domain resolution (and `export_surface.py`'s
  `contract=exports` type-closure step, which has always shared the same
  closure-walk function) now traverses `AbiSnapshot.surface_graph` --
  `compare/surface_graph.py`'s unconditional L0-L2 evidence graph -- instead
  of `surface.py`'s previous, independent regex-based re-parse of
  `fn.return_type`/`rec.fields`/`rec.bases`/typedef targets. `surface.py`'s
  own closure-walk implementation is deleted (its `compute_public_surface()`
  is now a thin wrapper); the algorithm and its `PublicSurface` result type
  moved to a new leaf module pair, `abicheck/policy/public_surface.py` /
  `abicheck/policy/public_surface_closure.py`, with `PublicSurfaceQuery`
  itself split into `abicheck/policy/public_surface_query.py` to avoid a
  real import cycle once `export_surface.py` started depending on the
  migrated closure walk directly. No observable behavior change for any
  existing caller: `compute_public_surface()`/`PublicSurfaceQuery`'s call
  shapes, and every `PublicSurface` field they fill, are unchanged, and the
  full FP-rate and per-tier-accuracy gates (plus every existing surface/
  export/contract-evaluation test) pass unmodified.
  - A real correctness hazard was found and closed during this migration,
    not merely a refactor risk averted on paper: two declarations can share
    one *approximate* graph node id when neither carries a resolved
    `entity_id` (e.g. two overloads with no mangled name to disambiguate
    them). Naively trusting the graph's per-node cache for such a
    collision let a public, narrow-signature overload appear to reference
    a hidden sibling's own private parameter type. `compare/surface_graph.py`
    now flags a colliding node (`identifiers_collision`), and the query
    falls back to recomputing that one declaration's own identifiers
    directly whenever the flag is set -- exactly the pre-migration
    behavior, preserved for the one case a shared-node graph cannot
    represent precisely. A regression test
    (`TestGraphNodeCollisionDoesNotBlurReachability` in
    `tests/test_policy_public_surface.py`) pins this directly.
  - A second correctness hazard, found by review after this migration
    landed (Codex, PR #979): `service_header_graph_attach._attach_header_
    graph` installs an L5 `surface_graph` on essentially every real dump,
    but deliberately never calls `build_public_surface_facts` itself (a
    documented, measured 47-96% header-graph-attach-cost regression from
    paying that walk on every dump). `resolve_surface_graph_nodes()` only
    rebuilt a graph when it was `None`, so this already-attached but
    evidence-incomplete graph was trusted as-is and every node read as
    referencing nothing -- silently collapsing the transitive type closure
    on the *ordinary, default* `--scope-public-headers` dump path, not
    merely a stale-schema edge case. Fixed by having
    `resolve_surface_graph_nodes()` always call `build_public_surface_facts`
    on the resolved graph -- an idempotent, evidence-preserving merge that
    enriches an already-attached graph's existing nodes in place (never
    discarding `_attach_header_graph`'s own L5 edges/facts) instead of only
    covering the `None` case, and now the "later phase" the attach site's
    own docstring always said this cost was deferred to. Regression tests
    (`TestUnpopulatedAttachedGraphIsBackfilled`,
    `TestStrippedGraphAttrsAreReconstructedNotTrusted` in
    `tests/test_policy_public_surface.py`) cover both the never-populated
    and stripped-attrs shapes.
  - Two further review findings on the same PR, both fixed in the same
    pass: (1) the module split dropped `PublicSurfaceQuery`/
    `resolve_public_surface`/`PublicSurfaceResolution` from
    `policy/public_surface.py`'s own namespace, breaking that historical
    import path for any existing consumer -- restored via a lazy
    `__getattr__` re-export shim (the same pattern already used at the tail
    of `cli_buildsource.py`) for the two moved names, plus a direct
    `PublicSurfaceResolution = PublicSurface` alias, all three now back in
    `__all__` for `from ... import *` compatibility too. (2)
    `_attach_header_graph` finalizes the L5 graph (stamping `graph_id`/
    `coverage`) *before* installing it as `snap.surface_graph`; enriching
    that same graph in place with public-surface nodes/edges without
    re-finalizing left a content-addressed `graph_id` that no longer
    matched the graph's own, now-larger content on a later
    `save_snapshot`/`to_dict`. `resolve_surface_graph_nodes()` now
    re-finalizes after enrichment. Regression tests:
    `TestPublicSurfaceBackCompatReexports`,
    `TestSurfaceGraphRefinalizedAfterEnrichment` in
    `tests/test_policy_public_surface.py`.
