### Added

- **All eleven cross-source hygiene checks now run automatically inside
  `compare()`.** `header_build_context_mismatch`, `odr_type_variant`,
  `identity_collision_detected`, `compile_context_conflict`, and
  `source_surface_dso_mismatch` join the six checks already migrated
  (`unversioned_exported_symbol`, `private_header_leak`,
  `exported_not_public`, `public_not_exported`, `rtti_for_internal_type`,
  `public_to_internal_dependency`) on `compare()`'s automatic
  `cross_source_checks` pipeline stage (default `True`, no CLI/API/Action
  opt-in flag — ADR-068 D4/D5). Each is evaluated independently on OLD and
  NEW and folded into a single, evolution-stated finding
  (`not_evaluated`/`introduced`/`resolved`/`persistent` — ADR-068 D3), so a
  pre-existing hygiene problem a stripped/ELF-only baseline can't confirm
  never misreads as newly introduced. `odr_type_variant`,
  `identity_collision_detected`, and `compile_context_conflict` each needed
  their own composite per-finding identity (beyond a bare exported symbol
  name) — a build target's compile-context conflicts, an ODR conflict's own
  header, and a USR-collision's own identity key can each carry more than
  one distinct finding sharing one `symbol`.

### Fixed

- **`scan --against` no longer silently double-reports a migrated
  cross-source check in its old/new diff.** `scan`'s own dedicated
  `crosscheck` report block (with `--crosscheck KEY=error` promotion) was
  always meant to be the sole source of cross-source-check findings for a
  baseline comparison — `_run_baseline_compare`'s own docstring documents
  this as advisory-by-default unless explicitly promoted. Once a check
  landed on `compare()`'s own automatic `cross_source_checks` stage, its
  internal baseline `compare()` call started producing the identical finding
  a second time; an `API_BREAK`-severity check (`header_build_context_
  mismatch`) newly exposed this as a real regression (a clean, unpromoted
  baseline started exiting non-zero), where a `RISK`-severity one had stayed
  silent since RISK findings don't raise the legacy exit code. `scan
  --against` now strips the automatically-produced findings from its own
  diff before computing the verdict, restoring the documented, advisory-only
  behavior for every migrated check.
