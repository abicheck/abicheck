### Changed

- **Four root-level report-formatting modules are now classified `report`
  by this PR's own diff** (ADR-061): `html_template.py`,
  `junit_coverage_warnings.py`, `report_classifications.py`,
  `report_correlation.py`. (Fourteen candidates were reviewed in total; six
  were classified `report` and then reverted after Codex review found they
  compute policy/gate decisions rather than only rendering an
  already-computed result — see below, and see further below for why the
  merged state on `main` carries all six of them anyway, via a sibling
  PR. `appcompat_html.py`, `sarif.py`, `stack_html.py`, `stack_report.py`
  were already `report`-classified on `main` by other, already-merged
  ADR-061 PRs by the time this one was first rebased, so they are not new
  entries from this diff even though they were part of the original
  fourteen-candidate review pass.) Pure data-only ledger change to
  `architecture/modules.yaml`
  — 0 architecture errors both before and after; none of these four import
  anything outside `report`'s allowed targets (`model`, `compare`, `policy`,
  `workflows`) once their own unclassified-but-harmless dependencies
  (`checker.py`, `checker_policy.py`, `demangle.py`, `contract_gating.py`,
  `semver.py`, `impact/`, `binder.py`, `resolver.py`, `stack_checker.py`) —
  all still unclassified themselves, so `_layer_for` returns `None` for
  them and the architecture gate's `dependency-direction` check does not
  apply — are skipped by that check.

  **Six candidates were reverted after Codex review, each for the same
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
  - `report_model.py`: `ReportModel.classify()` and `verdict_of()` both
    call `result._effective_verdict_for_change(c)`
    (`checker_types.DiffResult`'s own method), which is a thin wrapper —
    `from .reclassify import effective_verdict_for_change; return
    effective_verdict_for_change(change, policy=self.policy,
    kind_sets=self._effective_kind_sets(), policy_file=self.policy_file)`
    — around the exact same `reclassify.effective_verdict_for_change`
    policy resolver `severity.effective_verdict_for_change` re-exports and
    `reporter_markdown.py`'s already-reverted `ShowOnlyFilter._check_
    severity()` calls directly. Reaching the decision through `DiffResult`'s
    own bound method rather than importing `severity`/`reclassify` directly
    is a different code path to the same live, policy-file-aware
    recomputation, not a read of an already-resolved, cached value — it is
    re-invoked, unmemoized, on every call.
  - `report_summary.py`: `compatibility_metrics()` directly imports and
    calls `severity.effective_verdict_for_change()` per change when
    `policy`/`kind_sets`/`policy_file` is given (its own docstring: "Passing
    *kind_sets*... and/or *policy_file* makes this metric agree with the
    verdict by counting each change's effective verdict instead of its raw
    kind"), and `build_summary()` always calls it this way, via
    `result._effective_kind_sets()`/`result.policy`/`result.policy_file` —
    the identical decision function `report_model.py` above reaches one hop
    removed.

  This PR itself left these six unclassified rather than force-classifying
  them either way — a correct fix would split the gate/severity computation
  out into `policy`/`workflows` and have the renderer consume the resolved
  decision, which is a real code change, not a ledger edit, and out of scope
  for this PR. **They are classified `report` in the merged state anyway**:
  a sibling ADR-061 PR (`764ebe4a2`, "classify 22 more flat modules") landed
  on `main` first and swept all fourteen of the original candidates into
  `report` — including these same six, plus `pr_comment.py`/
  `pr_comment_base.py`/`pr_comment_scan.py`/`root_cause_evidence.py` — using
  only `check_architecture.py`'s import-direction check as its verification,
  which cannot see this role-mismatch concern (`report`'s `may_import`
  already includes `policy`, so a report module calling straight into
  `severity.py`/`reclassify.py`/`exit_decision.py` produces no forbidden
  edge either way). Rebasing this PR onto that state via a plain merge
  therefore carries all six back in through `architecture/modules.yaml`'s
  `report.legacy_paths`, unioned with this PR's own four — resolved that
  way per this session's own instructions (union on conflict, remove only
  on a genuine new `check_architecture.py` error, which this merge does not
  produce). The role-mismatch finding above stands as documented reasoning
  either way; closing it for real still needs the same policy/workflows
  split this paragraph already describes, not a ledger reshuffle in either
  direction. `sarif.py` (already `report`-classified on `main` before this
  PR, not touched by this diff) has the identical shape
  (`classify_effective_change`, `gate_contribution_for_change`,
  `compute_gate_decision` all called directly) and is likely a further
  pre-existing instance of the same issue — flagged here for visibility,
  not reclassified, since it isn't part of this PR's own diff.

  `report_classifications.py` was *also* named in the same Codex finding
  that flagged `report_model.py`/`report_summary.py` above, but reading it
  directly does not support the same conclusion: `is_breaking()`,
  `category()`, `severity()`, and their backing frozensets
  (`BREAKING_KINDS`, `HIGH_SEVERITY_KINDS`, `MEDIUM_SEVERITY_KINDS`,
  `CATEGORY_PREFIXES`, ...) are all static, module-level lookup tables —
  none consult a `PolicyFile`/`policy_file` override, none call
  `severity.py`/`reclassify.py`/`exit_decision.py`, and none feed the
  compatibility verdict or process exit code (confirmed by tracing every
  import site: `html_report.py`'s section grouping, `reporter_markdown.
  py`'s `ENVIRONMENT_DRIFT_KINDS` bucketing, and `compat/xml_report.py`'s
  ABICC-style High/Medium/Low severity column are all display-only). This
  is the same shape as the `VERDICT_PRESENTATION` table `report_model.py`
  itself keeps for verdict-axis labels — a fixed, non-policy-file-aware
  presentation mapping, not a live per-run decision — so it stays
  classified `report`.

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
