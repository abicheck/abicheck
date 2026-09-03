### Fixed

- Reverted a role-mismatch regression in `architecture/modules.yaml`
  reintroduced by two already-merged classification PRs. PR #927
  ("classify batch 4") re-added `pattern_verdicts.py` to `policy.
  legacy_paths`, and a sibling PR (commit `764ebe4a2`) re-added six
  modules to `report.legacy_paths` — both after other, separately
  in-flight PRs (#912, #913) had deliberately excluded these same
  modules from `policy`/`report` for a documented reason: each computes
  a live policy/severity/gate decision, or does raw old/new-snapshot
  change detection, rather than only rendering or matching what an
  earlier stage already resolved. Re-verified every one of the seven
  findings directly against the current source (not assumed from the
  earlier PRs' descriptions) before reverting:
  - `pattern_verdicts.py` — `apply_pattern_verdicts()`'s
    `_emit_lost_invariants()`/`_emit_new_antipatterns()` compare
    old/new snapshots directly and construct brand-new `Change(...)`
    objects; this is `compare/`-shaped raw-change detection per
    AGENTS.md's task-routing table, not `policy/`-shaped relevance/
    severity/gating work. Only its third phase, `_modulate_change()`,
    is genuinely policy-shaped, and it was left classified.
  - `html_report.py` — `_gate_card_html()` calls
    `severity.compute_gate_decision()`, a live gate computation.
  - `junit_report.py` — `_is_failure()` calls
    `severity.classify_effective_change()`; `_add_contract_properties()`
    calls `severity.gate_contribution_for_change()`.
  - `reporter_contract_blocks.py` — `add_contract_context()` calls
    `exit_decision.resolve_compare_exit_decision()`.
  - `reporter_markdown.py` — `to_stat()`/`_severity_merge_effect()` call
    `severity.compute_exit_code()`; `ShowOnlyFilter._check_severity()`
    calls `severity.effective_verdict_for_change()`.
  - `report_model.py` — `ReportModel.classify()`/`verdict_of()` call
    `result._effective_verdict_for_change()`, a thin wrapper around
    `reclassify.effective_verdict_for_change()`.
  - `report_summary.py` — `compatibility_metrics()` calls
    `severity.effective_verdict_for_change()` directly.

  All seven modules are removed from their `legacy_paths` list again,
  restoring them to the flat, unclassified root-module inventory
  (`architecture/modules.yaml`'s `legacy_root_modules`) until a genuine
  `compare`/`policy` split lands for each. `check_architecture.py` and
  `check_ai_readiness.py` stay at 0 errors with this reverted.
