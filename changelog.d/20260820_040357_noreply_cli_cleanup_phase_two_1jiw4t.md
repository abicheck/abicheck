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
  tier's `surface.explicit_scope` now also falls back to
  `result.explicit_scope_source_sha256` (the same way `suppressions`
  already falls back to `result.suppression_source_sha256`), since a
  `--pack`-only run stamps `evaluation_config` without ever building a
  `PersistedContractContext` to merge the observed `--post-manifest`
  scope into it — leaving that field unset even though `checker.compare()`
  already resolved and recorded the real scope digest on the result.
