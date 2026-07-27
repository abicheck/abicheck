<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049 Phase 3 shadow evaluator: two more correctness gaps closed**
  (no behavior change outside this still-unwired shadow module):
  - A finding already demoted by `compare --post-manifest`
    (`FilterNonPublicSurface._run_allowlist`'s own `"not in POST manifest
    committed surface"` reason — a confident, closed-domain exclusion for a
    concrete export absent from the committed allowlist) was not recognized
    as terminal, so it could fall through to a fresh header-surface
    recomputation and be wrongly reclassified `IN_CONTRACT` whenever the
    symbol also happened to be header-resolvable. Now recognized as
    terminal, matching ADR-049 D2's "exact manifests" evidence provider.
  - Two distinct records/enums sharing one bare tail (e.g. `ns1::Mode`/
    `ns2::Mode`, both spelled bare `Mode`) reached from a public signature
    via the ambiguous bare tail land *both* qualified names in
    `public_types`, but `ambiguous_type_names` only ever records the bare
    tail itself, not the qualified names. A member-level finding
    owner-stripped to one of the qualified names (`ns1::Mode`) therefore
    matched `public_types` directly and was wrongly confirmed, even though
    the public signature never disambiguated which of the two records it
    actually reaches. `_in_surface_result_is_confirmed()` now also rejects
    a qualified candidate whose own trailing tail is ambiguous.
