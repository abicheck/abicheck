### Fixed

- **Internal: redirect the remaining `contract_gating.py`/`reclassify.py`
  internal callers to their real ADR-061 gap B owners, and fix two
  perf-classifier omissions.** Three more real Codex findings on this PR's
  own reconciliation work:
  - `contract_gating.py`: every `report`/`workflows`/`policy`-classified
    internal caller (`reporter_markdown.py`, `junit_report.py`, `sarif.py`,
    `annotations.py`, `html_report.py`, `reporter.py`, `report_model.py`,
    `bundle_models.py`, `contract_scoped_promotion.py`,
    `contract_pipeline.py`, `semver.py`, `appcompat.py`) now imports
    `policy.contract_finding_relevance` directly. Only `checker_types.py`
    (`model`-classified, `model -> policy` forbidden) and
    `cli_scan_baseline.py` (`frontends`-classified, `frontends -> policy`
    forbidden) remain on the facade.
  - `reclassify.py`: `policy_file.py`'s three lazy, function-local imports
    now resolve `policy.reclassify` directly — the stale comment claiming
    a `reclassify -> suppression -> checker_types` cycle motivated the
    original facade routing is corrected too (`policy.reclassify` dropped
    that import when it moved to `.selectors`; the *live* cycle concern is
    a different one, `checker_types.py` importing `PolicyFile` from this
    same module). `exit_decision.py`, `effective_config_digest.py`, and
    the same report-layer modules above are redirected the same way.
  - `scripts/classify_perf_paths.py` and `scripts/skill_eval_surface.py`
    each listed only `policy/classification.py` as `checker_policy.py`'s
    real owner, missing its evidence-status half
    (`policy/evidence_status.py`, whose `is_cross_source_resolved()` is
    called per finding on the benchmarked gate path). Both routing sets
    now include it.
