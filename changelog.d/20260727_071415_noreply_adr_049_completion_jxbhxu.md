<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **ADR-049 Phase 2: canonical identity wired into cross-detector dedup**
  (no reported-finding behavior change): `diff_filtering.py`'s
  `_deduplicate_cross_detector()` now uses
  `finding_identity.resolve_change_identity()` as its dedup key instead of
  a hand-rolled `(change_category, symbol)` tuple. `resolve_change_identity`
  already collapses the identical kind pairs onto one shared category
  discriminator, so the swap is behavior-preserving for every kind that
  stage collapses (rich-vs-L0 function/variable add/remove,
  symbol-version-node pairs) — verified by a new dedicated unit suite
  (`tests/test_diff_filtering_cross_detector_identity.py`) and by the
  existing FP-rate-gate/tier-accuracy-gate/golden/detector-oracle/
  detector-property suites, all unchanged after the wiring.
  `diff_symbols.py`'s own old/new function and variable matching remains
  unwired (deliberately deferred — a substantially larger refactor against
  hand-tuned matching logic, not a drive-by change).
- **ADR-049 Phase 2: end-to-end fact-conservation property test**: a new
  Hypothesis suite (`tests/test_fact_conservation_properties.py`, `slow`)
  exercises the real `checker.compare()` pipeline end to end — for
  randomized old/new public function and variable sets, every removed
  symbol always surfaces as a removal finding referencing it, and every
  retained symbol never does.
