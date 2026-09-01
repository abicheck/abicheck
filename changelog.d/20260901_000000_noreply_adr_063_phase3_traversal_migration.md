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
    landed: a persisted `surface_graph` from the schema-v29 plumbing (which
    landed one version before this traversal started reading it) is already
    non-`None`, but its nodes predate the `referenced_identifiers`/
    `identifiers_collision` attrs entirely -- so `resolve_surface_graph_
    nodes()` was trusting such a graph unconditionally and reading every
    node as referencing nothing, silently collapsing the transitive type
    closure for any snapshot round-tripped through an older abicheck.
    Schema bumped to v30; a new `AbiSnapshot.surface_graph_referenced_
    identifiers_reliable` flag (following the file's own established
    `header_cv_facts_reliable`/`castxml_var_access_facts_reliable` pattern)
    marks such a snapshot, and `resolve_surface_graph_nodes()` rebuilds the
    graph in memory instead of trusting the stale one. Regression test
    (`TestStalePersistedGraphIsNotTrustedForReachability` in
    `tests/test_policy_public_surface.py`) constructs a stale-but-otherwise-
    real graph and confirms a type only reachable through it survives.
