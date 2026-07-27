<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049 Phase 3 shadow evaluator: a one-sided public-root proof is now
  sufficient, not both sides** (opt-in via
  `compare(..., contract_evaluation=True)`; no default-path behavior
  change): `evaluate_change_contract_relevance` required *both*
  `surf_old`/`surf_new` to be header-resolvable before attempting any
  classification, downgrading to `UNKNOWN_UNRESOLVED` even when, e.g., a
  `FUNC_REMOVED` finding's old side definitively proved the function was
  public and only the new side lacked header evidence. A positive proof from
  one resolvable side is sufficient on its own; only a *negative* exclusion
  claim needs full two-sided evidence, and `classify_change_surface`'s own
  internal gate already guarantees it never confidently excludes when either
  side is unresolvable. Relaxed the blanket `and` gate to `or` (bail out
  only when *neither* side is resolvable), gaining a correctly-confirmed
  `IN_CONTRACT` in the one-sided case without ever risking a wrong
  `PROVEN_OUT_OF_CONTRACT`.

- **ADR-049 Phase 3 shadow evaluator: the `force_public_symbols` widening
  overlay is now respected**: `FilterNonPublicSurface`'s `_run_scope`/
  `_run_allowlist` keep a `force_public_symbols`-matched change in-surface
  unconditionally, bypassing their own demotion path -- such a change never
  gets a `surface_exclusion_reason` set, and the shadow evaluator had no way
  to know the overlay existed at all, so a from-scratch
  `classify_change_surface` recomputation could reach a private-header
  conclusion directly contradicting the pipeline's own forced-public
  decision for the same symbol. `evaluate_change_contract_relevance`/
  `evaluate_snapshot_pair_contract_relevance` now accept an optional
  `force_public_symbols` parameter, trusted at the same early point as
  `python_*`/`_NEVER_FILTER_KIND_NAMES`/the public-source-ABI kind set.
