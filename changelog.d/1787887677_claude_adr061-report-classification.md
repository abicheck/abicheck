### Changed

- **Fourteen root-level report-formatting modules are now classified
  `report`** (ADR-061): `html_report.py`, `html_template.py`,
  `appcompat_html.py`, `junit_report.py`, `junit_coverage_warnings.py`,
  `report_classifications.py`, `report_correlation.py`, `report_model.py`,
  `report_summary.py`, `reporter_contract_blocks.py`, `reporter_markdown.py`,
  `sarif.py`, `stack_html.py`, `stack_report.py`. Pure data-only ledger
  change to `architecture/modules.yaml` — 0 architecture errors both before
  and after; none of these fourteen import anything outside `report`'s
  allowed targets (`model`, `compare`, `policy`, `workflows`) once their own
  unclassified-but-harmless dependencies (`checker.py`, `checker_policy.py`,
  `demangle.py`, `contract_gating.py`, `semver.py`, `impact/`, `binder.py`,
  `resolver.py`, `stack_checker.py`, and the several lazily-imported
  `contract_*`/`analysis_assurance`/`exit_decision`/`effective_config_digest`/
  `annotations` modules `reporter_contract_blocks.py` reaches — all still
  unclassified themselves, so `_layer_for` returns `None` for them and the
  architecture gate's `dependency-direction` check does not apply) are
  skipped by that check.

  Two of the sixteen candidate files were deliberately left unclassified,
  both for a role mismatch rather than an import violation:
  `stack_binding_diff.py` computes a real cross-environment symbol-binding
  diff (`diff_runtime_bindings()` builds `Change` objects from two resolved
  `DependencyGraph`s/`SymbolBinding` lists — matching bindings across
  environments, detecting a provider swap or a weak/strong resolution flip)
  rather than rendering an already-computed result — that is a detector, the
  same shape as `stack_report.py`'s own sibling `diff_*.py` modules under
  `compare`, not a report formatter; its immediate, format-only neighbor
  `stack_report.py` (which only ever renders `StackCheckResult`/`Change`
  objects someone else computed) was classified `report`, but
  `stack_binding_diff.py` itself was not. `appcompat.py` computes its own
  compatibility verdict for an application
  (`compute_verdict`/`impact.engine.assess_change` over the app's
  required-symbol intersection with a library `DiffResult`, per ADR-005)
  rather than only formatting an existing report — the module map's own "7.
  Application compatibility" section already lists it as a distinct
  pipeline stage from "6. Reporting". Its format-only sibling,
  `appcompat_html.py` (renders an already-computed `appcompat` result to
  HTML, no decision logic of its own, no import of `appcompat.py`), *was*
  classified `report`. Both residuals are intentional, ADR-061-established
  outcomes, not gaps to close in this PR.

  Verified: `python scripts/check_architecture.py` → 0 errors (before and
  after); `python scripts/check_ai_readiness.py` → 0 errors, warning count
  unchanged from base; `python scripts/adr_status_sync.py` → clean; `mypy
  abicheck/` → clean (no `.py` file touched); `pytest
  tests/test_architecture_check.py` → 40 passed; targeted keyword-selected
  tests (`appcompat`/`html_report`/`junit`/`report_summary`/
  `report_correlation`/`report_classifications`/`reporter_markdown`/
  `reporter_contract`/`sarif`/`stack_html`/`stack_report`/`stack_binding`)
  → 609 passed; full fast unit suite green.
