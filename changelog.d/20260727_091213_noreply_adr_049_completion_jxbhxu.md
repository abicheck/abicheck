<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049 Phase 3: shadow evaluator no longer conflates conservative
  retention with confirmed public membership** (no behavior change outside
  this still-unwired shadow module): `surface.classify_change_surface()`
  returns `(True, None)` both for genuine public-root/closure membership
  and for its own anti-hiding "cannot place this finding, so keep it"
  fallback (an implicated type entirely absent from either snapshot's type
  universe, or an internal-namespace type deferred to the internal-leak
  detector) -- `evaluate_change_contract_relevance()` was treating every
  `True` the same, silently upgrading unresolved evidence to `IN_CONTRACT`.
  A new `_in_surface_result_is_confirmed()` check distinguishes the two,
  downgrading the conservative-retention case to `UNKNOWN_UNRESOLVED`,
  while still trusting `_NEVER_FILTER_KIND_NAMES` (leak/`constant_*`
  findings) and `python_*`-prefixed findings unconditionally, since those
  are public by construction and would never appear in
  `public_symbols`/`public_types` at all.
