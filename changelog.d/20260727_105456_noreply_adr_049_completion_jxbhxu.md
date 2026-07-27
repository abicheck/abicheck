<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Pack manifests: `datetime` assignment values keep their `fold`**
  (`compatibility_evaluation_packs.py`): canonicalizing a `datetime.datetime`
  assignment value reconstructed it via the positional-args constructor
  without passing through `fold`, silently resetting an ambiguous local time
  (e.g. a DST fall-back instant) to `fold=0`. `datetime`'s own
  `__eq__`/`__hash__` ignore `fold`, so this was invisible to an ordinary
  equality assertion. Fixed by passing `fold=value.fold` through the
  reconstruction.

### Documentation

- **ADR-049 Phase 3 shadow evaluator (`compare(..., contract_evaluation=True)`,
  opt-in): documented, not fixed, a known over-rejection of exact qualified
  type references**: when a public signature names a type fully qualified
  (e.g. `ns1::Point`) and the snapshot separately contains an unrelated
  same-tail type (`ns2::Point`), the finding's exact, unambiguous match is
  currently rejected the same as a genuinely ambiguous bare reference would
  be — `_type_identifiers` derives the same bare tail from both an exact
  qualified reference and a truly ambiguous bare one, so the two routes are
  indistinguishable from `PublicSurface`'s current
  `public_types`/`ambiguous_type_names` alone. A precise fix needs new
  per-type provenance tracking in `surface.py`'s closure walk itself — the
  public-surface-scoping gate every other detector depends on, not a
  boundary specific to this shadow module — so it is documented as a known,
  conservative (never wrongly `IN_CONTRACT`) limitation and locked in with
  a regression test, rather than attempted as a drive-by fix here.
