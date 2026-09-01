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
  - Two further review rounds (Codex, PR #979) found the graph itself
    could not be trusted for this computation at all, in two different
    ways, and the fix that closes both simultaneously is described below
    rather than as two separate patches, since the final design supersedes
    two earlier, real, individually-fixed-then-superseded intermediate
    states (`git log` on this branch has the full round-by-round history;
    `docs/contribute/known-gaps.md`'s ADR-063 Phase 3 entry has the
    complete account). First, `service_header_graph_attach.
    _attach_header_graph` installs an L5 `surface_graph` on essentially
    every real dump without ever populating `referenced_identifiers`/
    `identifiers_collision` on its nodes, so trusting an attrs-less node
    as "references nothing" silently collapsed the transitive closure on
    the *ordinary, default* dump path. Second, even after enriching that
    graph, a schema-v29 or otherwise untrusted/adversarial snapshot could
    carry a stale or crafted `referenced_identifiers` fact at a confidence
    this module's own freshly-registered fact (always the lowest rank)
    cannot outrank -- the graph's cross-producer evidence-merge precedence
    would let the stale/poisoned value silently win over the correct one,
    the same collapsed-closure failure mode reached a different way.
  - **Fixed by removing the graph from this computation entirely.**
    `compare/surface_graph.py`'s `referenced_identifiers_by_node()` (now
    public, alongside its `ReferencedIdentifiers` return type) is a pure
    function of a snapshot's own current declarations, computed *before*
    any `GraphNode` is built. `policy/public_surface_closure.py`'s and
    `export_surface.py`'s closure-walk entry points now call it directly
    and thread the result through instead of reading
    `AbiSnapshot.surface_graph`/`GraphNode.attrs` at all --
    `resolve_surface_graph_nodes()` (the function that used to
    enrich/backfill the graph for this purpose) had no remaining caller
    once both sites switched, and is deleted rather than kept as unused
    surface. This closes the security concern outright (nothing is ever
    merged, so there is no evidence precedence for a stale or adversarial
    fact to win) and, as a direct consequence, removes essentially all of
    the `GraphNode`/`GraphFact`/evidence-merge construction cost from the
    hot path, confirmed by CI's own "Baseline regression (PR vs base)"
    gate going green on the commit that made this change. Regression tests:
    `TestClosureIgnoresSurfaceGraphEntirely` (including a deliberately
    adversarial, high-confidence poisoned fact),
    `TestPublicSurfaceBackCompatReexports`,
    `TestResolvePublicSurfaceIsNotIdentityCached` in
    `tests/test_policy_public_surface.py`.
  - The module split also dropped `PublicSurfaceQuery`/
    `resolve_public_surface`/`PublicSurfaceResolution` from
    `policy/public_surface.py`'s own namespace, breaking that historical
    import path for any existing consumer -- restored via a lazy
    `__getattr__` re-export shim (the same pattern already used at the tail
    of `cli_buildsource.py`) for the two moved names, plus a direct
    `PublicSurfaceResolution = PublicSurface` alias, all three now back in
    `__all__` for `from ... import *` compatibility too.
