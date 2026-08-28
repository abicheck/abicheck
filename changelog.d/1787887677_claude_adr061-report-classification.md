### Changed

- **Six root-level report-formatting modules are now classified `report`**
  (ADR-061): `html_template.py`, `junit_coverage_warnings.py`,
  `report_classifications.py`, `report_correlation.py`, `report_model.py`,
  `report_summary.py`. (Fourteen candidates were reviewed in total; four
  were classified `report` and then reverted after Codex review found they
  compute policy/gate decisions rather than only rendering an
  already-computed result — see below. `appcompat_html.py`, `sarif.py`,
  `stack_html.py`, `stack_report.py` were already `report`-classified on
  `main` by other, already-merged ADR-061 PRs by the time this one was
  rebased, so they are not new entries from this diff even though they were
  part of the original fourteen-candidate review pass.) Pure data-only
  ledger change to `architecture/modules.yaml` — 0 architecture errors both
  before and after; none of these six import anything outside `report`'s
  allowed targets (`model`, `compare`, `policy`, `workflows`) once their own
  unclassified-but-harmless dependencies (`checker.py`, `checker_policy.py`,
  `demangle.py`, `contract_gating.py`, `semver.py`, `impact/`, `binder.py`,
  `resolver.py`, `stack_checker.py`) — all still unclassified themselves, so
  `_layer_for` returns `None` for them and the architecture gate's
  `dependency-direction` check does not apply — are skipped by that check.

  **Four candidates were reverted after Codex review, each for the same
  reason: the module computes a policy/severity/gate decision itself rather
  than only formatting an already-computed one, which the routing table in
  `AGENTS.md` ("Decide relevance, suppression, classification, severity, or
  gating" → `policy/`) puts outside `report`'s remit.** Confirmed by reading
  each named function directly, not taken on the review's word alone:
  - `html_report.py`: `_gate_card_html()` calls
    `severity.compute_gate_decision()` directly and derives the CI gate's
    pass/fail state and exit code from it.
  - `junit_report.py`: `_is_failure()` calls `severity.classify_effective_
    change()` and applies `severity_config.level_for(...)` to decide
    per-finding JUnit failure itself; `_add_contract_properties()` calls
    `severity.gate_contribution_for_change()` to compute the persisted gate
    contribution.
  - `reporter_contract_blocks.py`: `add_contract_context()` calls
    `exit_decision.resolve_compare_exit_decision()` (now physically in
    `policy/exit_decision.py`, re-exported via a shim) and derives the
    exit-code scheme itself (`"severity" if severity_config is not None else
    "legacy"`).
  - `reporter_markdown.py`: both `to_stat()` and `_severity_merge_effect()`
    call `severity.compute_exit_code()` directly from `result.changes` +
    policy config; `ShowOnlyFilter._check_severity()` calls
    `severity.effective_verdict_for_change()` to re-derive an effective
    verdict for filtering.

  These four are left unclassified rather than force-classified either way
  — a correct fix would split the gate/severity computation out into
  `policy`/`workflows` and have the renderer consume the resolved decision,
  which is a real code change, not a ledger edit, and out of scope for this
  PR. `sarif.py` (already `report`-classified on `main`, not touched by
  this diff) has the identical shape (`classify_effective_change`,
  `gate_contribution_for_change`, `compute_gate_decision` all called
  directly) and is likely a pre-existing instance of the same issue —
  flagged here for visibility, not reclassified, since it isn't part of
  this PR's own diff.

  Two further candidates were deliberately left unclassified from the
  original review pass, both for a role mismatch rather than an import
  violation: `stack_binding_diff.py` computes a real cross-environment
  symbol-binding diff (`diff_runtime_bindings()` builds `Change` objects
  from two resolved `DependencyGraph`s/`SymbolBinding` lists — matching
  bindings across environments, detecting a provider swap or a weak/strong
  resolution flip) rather than rendering an already-computed result — that
  is a detector, the same shape as `stack_report.py`'s own sibling
  `diff_*.py` modules under `compare`, not a report formatter; its
  immediate, format-only neighbor `stack_report.py` (which only ever
  renders `StackCheckResult`/`Change` objects someone else computed) was
  classified `report`, but `stack_binding_diff.py` itself was not.
  `appcompat.py` computes its own compatibility verdict for an application
  (`compute_verdict`/`impact.engine.assess_change` over the app's
  required-symbol intersection with a library `DiffResult`, per ADR-005)
  rather than only formatting an existing report — the module map's own "7.
  Application compatibility" section already lists it as a distinct
  pipeline stage from "6. Reporting". Its format-only sibling,
  `appcompat_html.py` (renders an already-computed `appcompat` result to
  HTML, no decision logic of its own, no import of `appcompat.py`), *was*
  classified `report` (by an already-merged sibling PR, not this one).

  Verified: `python scripts/check_architecture.py` → 0 errors (before and
  after); `python scripts/check_ai_readiness.py` → 0 errors; `python
  scripts/adr_status_sync.py` → clean; `mypy abicheck/` → clean (no `.py`
  file touched); `pytest tests/test_architecture_check.py` → 40 passed;
  full fast unit suite green.
