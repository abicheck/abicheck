<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049 Phase 1: `--policy-file`-sourced `internal_namespaces` is now
  attributed to the `EXPLICIT_CLI` selector layer, not `PROJECT_CONFIG`**
  (`compatibility_evaluation_wiring.py`): `--policy-file` is a flag the user
  explicitly passes on this invocation, the same selection mechanism the
  existing `contract.mode` wiring already models at `EXPLICIT_CLI`/
  `LEGACY_ALIAS` tier -- not an implicitly-discovered project file, which is
  what `PROJECT_CONFIG` is for. Tagging it `PROJECT_CONFIG` meant a
  lower-precedence-by-mechanism candidate could silently outrank an
  explicitly user-selected manifest, and the provenance receipt itself
  misrepresented how the value was actually chosen.

- **ADR-049 Phase 3 shadow evaluator now honors `--post-manifest` committed
  symbols** (`contract_evaluation.py`/`checker.py`; no behavior change
  outside the opt-in `contract_evaluation=True` path):
  `_apply_contract_evaluation_shadow` forwarded `force_public_symbols` to
  the evaluator but not `public_surface_allowlist` (the `--post-manifest`
  committed-export set) -- so a symbol POST-manifest committed despite
  private-header provenance was wrongly stamped `PROVEN_OUT_OF_CONTRACT` by
  a fresh `classify_change_surface` recomputation blind to the commitment,
  inverting the pipeline's own kept-not-demoted decision for that exact
  finding. Fixed with the identical mechanism already used for
  `force_public_symbols`, matched by *exact* symbol name (not that
  overlay's suffix-tolerant rule) to mirror
  `FilterNonPublicSurface._run_allowlist`'s own exact-name-only contract.
