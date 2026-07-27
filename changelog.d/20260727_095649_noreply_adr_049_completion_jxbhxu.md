<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049 Phase 3 shadow evaluator: three more correctness gaps closed**
  (no behavior change outside this still-unwired shadow module):
  - `not-exported` was treated as a *terminal* surface-exclusion reason for
    `PUBLIC` mode, resolving a public-header-declared-but-non-exported
    entity (e.g. an inline or explicitly hidden-visibility function) to
    `PROVEN_OUT_OF_CONTRACT` -- contradicting ADR-049 D2, which defines
    `public` mode's domain to include "public declarations" independent of
    export status (that distinction is exactly what the separate `exports`
    mode is for). Moved to the weak-reason set, downgrading to
    `UNKNOWN_UNRESOLVED` instead.
  - A `python_*` finding (a distinct Python API/stub evidence axis) was
    downgraded to `UNKNOWN_UNRESOLVED` whenever the unrelated C/C++ header
    surface happened to be unresolvable, since the resolvable-surface gate
    ran before this evaluator ever consulted its own unconditional
    `python_*` trust. Moved the `python_*` check ahead of that gate so a
    definitive event like `PYTHON_API_FUNCTION_REMOVED` stays `IN_CONTRACT`
    regardless of C-header surface availability.
  - Two distinct records/enums sharing one bare tail name (e.g.
    `one::Point`/`two::Point`, both spelled bare `Point`) are kept in
    `public_types` by `compute_public_surface`'s own anti-hiding rule while
    being flagged in `ambiguous_type_names` -- but confirmation only checked
    `public_types` membership, treating that conservative closure expansion
    as proof of root membership. `_in_surface_result_is_confirmed()` now
    rejects a type-candidate match when every matching candidate is
    ambiguous on either snapshot side.

- **Pack manifests: `contract.overlays` is now order-insensitive**
  (`compatibility_evaluation_packs.py`): two contract packs assigning the
  same overlay set in a different order (`[a, b]` vs `[b, a]`) previously
  raised a spurious `PackConflictError`, since pack assignments preserve
  list order as a tuple while `ContractConfig.overlays` itself already
  canonicalizes (sorts + dedupes) the equivalent field. `contract.overlays`
  now gets the identical sort+dedupe canonicalization at the pack-manifest
  layer, matching `ContractConfig`'s own field 1:1.
