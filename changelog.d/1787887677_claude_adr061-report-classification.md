### Changed

- **This PR's net diff against `main` removes six modules from
  `report.legacy_paths`** (`architecture/modules.yaml`): `html_report.py`,
  `junit_report.py`, `report_model.py`, `report_summary.py`,
  `reporter_contract_blocks.py`, `reporter_markdown.py`. Pure data-only
  ledger change — 0 architecture errors both before and after.

  **History, since the diff this PR now carries is not the one its own
  commits originally set out to make.** This PR began by classifying
  fourteen root-level report-formatting modules as `report`
  (`architecture/modules.yaml`'s `report.legacy_paths`). Codex review on
  this same PR then found, and this PR's own commits confirmed by reading
  each named function directly, that six of those fourteen — the same six
  named above — each compute a live policy/severity/gate decision
  themselves rather than only rendering an already-resolved one, which the
  routing table in `AGENTS.md` ("Decide relevance, suppression,
  classification, severity, or gating" → `policy/`) puts outside `report`'s
  remit:
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
    (`checker_types.DiffResult`'s own method), a thin wrapper around the
    exact same live, policy-file-aware `reclassify.effective_verdict_for_
    change` resolver `severity.effective_verdict_for_change` re-exports —
    reached one hop removed via a bound method rather than a direct
    `severity`/`reclassify` import, but re-invoked, unmemoized, on every
    call, not a read of an already-resolved value.
  - `report_summary.py`: `compatibility_metrics()` directly imports and
    calls `severity.effective_verdict_for_change()` per change when
    `policy`/`kind_sets`/`policy_file` is given, and `build_summary()`
    always calls it this way via `result._effective_kind_sets()`/
    `result.policy`/`result.policy_file` — the identical decision function
    `report_model.py` reaches one hop removed.

  This PR reverted all six from `report.legacy_paths` for that reason,
  leaving four other, genuinely display-only candidates classified
  (`html_template.py`, `junit_coverage_warnings.py`,
  `report_classifications.py`, `report_correlation.py` — static
  presentation tables and formatting helpers with no live policy-file-aware
  decision of their own, confirmed the same way).

  **A sibling ADR-061 PR (`764ebe4a2`, "classify 22 more flat modules")
  then merged to `main` first**, sweeping all fourteen of this PR's
  original candidates into `report.legacy_paths` — including the six
  role-mismatched ones this PR had just reverted, plus `pr_comment.py`/
  `pr_comment_base.py`/`pr_comment_scan.py`/`root_cause_evidence.py` (not
  part of this PR's own review scope; checked separately here and found to
  be genuinely display-only — none imports or calls `severity`/
  `reclassify`/`exit_decision` at all, only a few docstring/comment
  mentions of `severity.*` describing behavior they consume as an
  already-computed value, so they're left classified as-is). Merging
  `main` into this branch therefore adopted `main`'s version of
  `architecture/modules.yaml` wholesale: since `main`'s `report.legacy_
  paths` already carried both this PR's own four genuine additions *and*
  the six reverted ones, the merge produced **no net diff** against `main`
  at all for a time — this PR's own commits after the merge (visible in
  its history as the "revert report_model.py/report_summary.py" and
  "revert 4 mixed policy/gate modules" commits) were re-deriving content
  `main` already had, which is why a later Codex review round correctly
  flagged this fragment as describing a no-op: at that point in the PR's
  history, `architecture/modules.yaml` had genuinely stopped differing
  from `main` in any way this fragment claimed.

  **This entry's own text is therefore no longer a report of what this
  PR's commit history did — it's a description of this PR's actual,
  current net diff**, restored by explicitly re-removing the same six
  modules one more time from the post-merge state (which had regained
  them from the sibling PR), specifically to preserve the reasoning above
  now that it would otherwise be silently lost. The four genuine additions
  (`html_template.py`, `junit_coverage_warnings.py`,
  `report_classifications.py`, `report_correlation.py`) are **not** part
  of this PR's net diff either way, since `main` already carries them via
  the sibling PR before this PR's base — nothing to add or remove for
  those four. Closing the role-mismatch finding for real still needs the
  same `policy`/`workflows` split the original finding already describes
  (splitting the gate/severity computation out of each of the six modules
  and having the renderer consume the resolved decision), not a ledger
  reshuffle in either direction — this PR does not attempt that split.

  **This commit's diff against `main` is itself the fix, and merging this
  PR applies it directly.** This branch is based on `main` commit
  `c18d24415`, and its net diff removes the same six modules from
  `report.legacy_paths` that the sibling PR (`764ebe4a2`) reintroduced —
  merging this PR into `main` therefore reverts them on `main` itself, with
  no separate follow-up PR needed for this overlap. Note there is also a
  separate, independently-created PR (#934) that reverts the same six
  modules plus `pattern_verdicts.py`/`policy.legacy_paths` as part of a
  broader fix bundling both the report- and policy-layer regressions
  against `main` directly — it was opened before this PR's own revert was
  known to reach `main` on merge, not because this PR's fix couldn't reach
  `main` without it. Whichever of the two merges first, the other becomes a
  no-op for the six overlapping modules; #934's extra `pattern_verdicts.py`/
  `policy.legacy_paths` changes are outside this PR's scope either way.

  `sarif.py` (already `report`-classified on `main` before this PR, not
  touched by this diff) has the identical shape (`classify_effective_
  change`, `gate_contribution_for_change`, `compute_gate_decision` all
  called directly) and is likely a further pre-existing instance of the
  same issue — flagged here for visibility, not reclassified, since it
  isn't part of this PR's own diff either.

  Two further candidates were deliberately left unclassified from the
  original review pass, both for a role mismatch rather than an import
  violation: `stack_binding_diff.py` computes a real cross-environment
  symbol-binding diff (`diff_runtime_bindings()` builds `Change` objects
  from two resolved `DependencyGraph`s/`SymbolBinding` lists) rather than
  rendering an already-computed result — a detector, not a report
  formatter; its format-only sibling `stack_report.py` was classified
  `report`. `appcompat.py` computes its own compatibility verdict for an
  application (`compute_verdict`/`impact.engine.assess_change`, per
  ADR-005) rather than only formatting an existing report; its format-only
  sibling `appcompat_html.py` (already `report`-classified on `main`
  before this PR) was left as-is.

  Verified: `python scripts/check_architecture.py` → 0 errors (before and
  after); `python scripts/check_ai_readiness.py` → 0 errors; `mypy
  abicheck/` → clean (no `.py` file touched); `pytest tests/test_
  architecture_check.py` → 40 passed; full fast unit suite green.
