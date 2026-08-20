### Added

- **`effective_config_digest`/`effective_config_fields`** — every
  `compare`/`compare-release`/`scan --against` JSON report now carries a
  `sha256:...` fingerprint of the resolved gate/policy/surface/contract
  configuration the comparison actually ran under, plus the named field
  dict it was hashed from (so a mismatch between two reports can be
  attributed to a specific field rather than read as an opaque hash — the
  same `profile_fingerprint`/`scope_fingerprint` precedent already used
  elsewhere). Computed once, identically, for all three front ends (CLI
  cleanup phase two, "PR B"). `report_schema_version` 2.46,
  `scan_schema_version` 1.20. The field set also now covers
  `--require-complete-analysis` (`gate.require_complete_analysis`), and the
  directory/package release fan-out's `--output-dir` sibling summary
  document (`summary.json`) carries both effective-config fields too, not
  just the primary release JSON. The baseline tier's `policy.base` now
  records a recognized built-in policy's full `id@version:sha256` identity
  (not just its bare name), matching the rich tier. Both fields are omitted
  (schema-optional, like `exit`) from `compat check --report-format json`
  output, since that front end's own transform options (`-strict`,
  `-source`/`-binary`, ...) aren't represented by this digest. The field
  set also now covers ADR-043 `--used-by`/`--required-symbol(s)` scoped
  gates (`gate.scope`), and the rich tier's gate axes
  (`gate.exit_code_scheme`/`gate.severity.*`) now always come from the
  caller's own already-resolved severity/exit-code-scheme (the same pair
  used for the `exit` block) rather than from the resolved
  `CompatibilityEvaluationConfig` directly — closing a real bug where a
  `--pack`-only `scan --against` recorded its digest from `resolve_scan_
  config`'s deliberately gate-blanked config instead of the run's real
  `--severity-preset`/`--exit-code-scheme`. Two more fixes: the rich tier
  now prefers `contract_context`'s merged, observed-overlay config (which
  can carry a `--post-manifest` overlay no front-end input model
  describes) over the unmerged `evaluation_config` copy whenever both
  exist; and `policy.reclassify` no longer sorts its encoded rules, since
  `reclassify` is first-match-wins in policy-file order and two
  order-swapped, overlapping rules can select a different verdict. A new
  `DiffResult.explicit_scope_source_sha256` field (populated by
  `checker.compare()` from `force_public_symbols`'s own content) lets the
  baseline tier's `surface.explicit_scope` distinguish two ordinary
  comparisons (neither `--contract` nor `--pack`) that resolve different
  `--public-symbols-list`/`.abicheck.yml` `scope.public_symbols` roots,
  which was previously hard-coded empty. That field now also folds in
  `--post-manifest`'s resolved `public_surface_allowlist` (a second,
  independent explicit-scope axis that reaches `compare()` the same way,
  with neither `--contract` nor `--pack` involved either) via a keyed JSON
  encoding rather than a delimiter-joined string, so the two axes can't
  collide with each other or with a same-content-different-shape input.
  `public_surface_allowlist` is gated on `is not None` rather than
  truthiness, since a `--post-manifest` committing to zero exports
  (`public_surface_allowlist=set()`) is a real, distinct, active scope
  from no manifest at all — the same `is not None` rule the comparison's
  own `scope_active` check already used for this parameter. The rich
  tier's `surface.explicit_scope` now *merges*
  `resolved_config.surface.explicit_scope` (which covers only
  `--public-symbols-list`/`.abicheck.yml`'s `scope.public_symbols`) with
  `result.explicit_scope_source_sha256` (which independently covers both
  that same axis and `--post-manifest`), rather than falling back to only
  one — `force_public_symbols` is threaded into `compare()`
  unconditionally, so a single `--pack`-only run combining
  `--public-symbols-list` and `--post-manifest` can populate both sources
  at once, and a plain `or` fallback would silently drop whichever axis
  lost the fallback race. Also: `explicit_scope_source_sha256`'s own
  hashing now reuses the shared `contract_evidence_collect.content_digest`
  primitive instead of a second hand-rolled convention, and a tautological
  test assertion (`a != b or (c != d)`, which never fails on its own) was
  tightened to assert the specific field it claimed to check. The field set
  also now covers ADR-027 A4 pattern-aware verdict modulation
  (`policy.pattern_verdicts`, a new `DiffResult.pattern_verdicts_enabled`
  field): `--pattern-verdicts`/`--explain-patterns` can modulate a
  finding's verdict and the process exit but was previously unrepresented,
  so two otherwise-identical runs differing only in this flag collided on
  the digest whenever no idiom/antipattern happened to match (the
  applied-modulation ledger alone is indistinguishable from the flag never
  having been set). Three more checker-level axes were added in the same
  shape: `policy.collapse_versioned_symbols` (`--collapse-versioned-
  symbols`, which can remove a versioned symbol-version remove/add pair
  entirely, turning an otherwise `BREAKING` verdict non-breaking),
  `policy.surface_metrics` (`--surface-metrics`, which appends suppressible
  aggregate-drift findings and can flip `NO_CHANGE` to `COMPATIBLE`), and
  `policy.env_matrix` (a new `DiffResult.env_matrix_source_sha256` field —
  a content digest of the resolved `--env-matrix`, since its runtime floors
  can reclassify a version-requirement finding, e.g. a GLIBC floor turning
  a RISK into `BREAKING`, and add deployment findings). One more axis in
  the same shape: `policy.reconcile_build_context`
  (`--reconcile-build-context`, a new `DiffResult.
  reconcile_build_context_enabled` field), since it can move a phantom
  breaking finding from `kept` into the reconciliation audit bucket,
  changing the verdict and exit code. A new `surface.scope_to_public_
  surface_requested` field (`DiffResult.scope_to_public_surface_
  requested`) records the *raw* `--scope-public-headers` value, distinct
  from the existing `surface.scope_to_public_surface`, which
  `checker.compare()` stamps with the *derived* `scope_active =
  scope_to_public_surface or public_surface_allowlist is not None` and
  therefore reads `True` whenever a `--post-manifest` allowlist is active
  regardless of the raw flag —
  `post_processing.FilterNonPublicSurface._run_allowlist` only honors the
  `force_public_symbols` widening overlay when the raw flag is true, so
  two runs sharing the same POST manifest and forced-public symbols but
  opposite raw `--scope-public-headers` settings previously published an
  identical digest despite retaining genuinely different findings.
