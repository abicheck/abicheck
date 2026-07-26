<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **ADR-049 Phase 2: tiered flat-finding identity resolver** (no behavior
  change): `abicheck/finding_identity.py` adds
  `resolve_function_identity`/`resolve_variable_identity`/
  `resolve_change_identity`, computing a canonical (verified mangled name)
  / normalized (qualified-name + kind + parameter-type signature) / reduced
  (source-relative alias, synthetic sha256 fallback) tiered identity for
  functions, variables, and already-emitted `Change` findings. Generalizes
  the mangled-primary + name-based extern-C fallback already hand-rolled in
  `diff_symbols._diff_functions` into one documented, independently-tested
  primitive, mirroring the "most specific available identity,
  ambiguity-safe fallback" principle ADR-045 established for flat type
  matching (`diff_helpers.TypeMap`) and ADR-048 established for L5
  source-graph nodes (`buildsource/entity_identity.py`). Deliberately not
  wired into `diff_symbols.py`'s old/new matching or
  `diff_filtering.py`'s cross-detector dedup key yet — this is the pure
  identity primitive Phase 2's fact-conservation gate will consume; see
  `docs/contribute/plans/public-contract-default.md` for remaining work.
