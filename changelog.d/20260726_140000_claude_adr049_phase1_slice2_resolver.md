<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **ADR-049 Phase 1 slice 2: field-level precedence resolver** (no behavior
  change): `abicheck/compatibility_evaluation_resolver.py` adds
  `resolve_field()`, implementing ADR-049 D7's per-field precedence order
  (`explicit_cli`/`api_request` > `legacy_alias` > `run_recipe` >
  `run_profile` > `project_config` > `built_in_default`) over
  already-collected `FieldCandidate` values, plus the two D7 usage-error
  rules: conflicting values at the same precedence tier
  (`ConflictingFieldValuesError`), and a legacy alias disagreeing with an
  explicit CLI/API value (`LegacyAliasConflictError`, with an opt-out for
  the documented `--policy`/`--policy-file` compatibility exception).
  Equivalent duplicate values at the same tier are accepted, matching D7.
  Conflict validation checks every populated precedence tier, not only the
  winning one, so a conflict shadowed behind a higher-precedence override is
  still caught immediately. A candidate whose `SelectorLayer` isn't covered
  by any precedence tier now raises a `ValueError` instead of silently
  vanishing from resolution — `SelectorLayer` is documented as extensible,
  so this guards against a future new layer being added there without a
  matching resolver update. `resolve_field()` also takes an
  `allow_run_profile` flag (default `False`): D7 scopes `run_profile`
  precedence to execution fields (depth, format, budget, workflow) only, so
  a `RUN_PROFILE` candidate for a field that hasn't opted in now raises
  instead of silently participating in a semantic field's precedence. The
  `--policy`/`--policy-file` exception path now records the suppressed
  legacy candidate's provenance on `ValueProvenance.shadowed_legacy`
  (D7: "provenance records the file-selected effective base and the
  shadowed `--policy` input"), instead of discarding it. This is pure
  resolution logic only — no front end (CLI, `.abicheck.yml`, service/API)
  constructs `FieldCandidate`s from real input yet; see
  `docs/contribute/plans/public-contract-default.md` for the remaining
  Phase 1 wiring work.
