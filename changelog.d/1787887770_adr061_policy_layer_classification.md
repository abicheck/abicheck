### Changed

- ADR-061 continuation: classified 7 more root-level modules into the
  `policy` responsibility layer in `architecture/modules.yaml`
  (`compatibility_evaluation_config.py`, `contract_evaluation.py`,
  `contract_evidence_collect.py`, `contract_pipeline.py`,
  `export_surface.py`, `pattern_verdicts.py`, `suppression_yaml.py`) —
  pure data-only ledger change, `check_architecture.py` reports 0 errors
  both before and after. Each classified file's own imports resolve only
  to `model`/`compare` (already-classified) or to a currently-unclassified
  sibling — the exact set `policy`'s `may_import: [model, compare]` allows.

  A much larger candidate set was investigated
  (`policy_file.py`, `suppression.py`, `contract_relevance_types.py`,
  `compatibility_evaluation_resolver.py`, `compatibility_evaluation_wiring.py`,
  `pack_application.py`, `contract_scoped_promotion.py`,
  `contract_coverage_ledger.py`, `contract_context.py`,
  `contract_context_io.py`, `contract_replay.py`, `contract_gating.py`,
  `reclassify.py`, `checker_policy.py`) and every one of them was left
  deliberately unclassified — not because their *role* doesn't fit
  policy (most are exactly the "decide relevance/suppression/
  classification/severity/gating" family the task-routing table
  describes), but because classifying them surfaces real, pre-existing
  forbidden edges the architecture gate would otherwise catch:

  - **`frontends -> policy` (11 of the 14 excluded files).** `cli_params.py`,
    `cli_compare_helpers.py`, `cli_compare_receipt.py`,
    `cli_compare_release.py`, `cli_compare_release_helpers.py`,
    `cli_helpers_compare.py`, `cli_buildsource_helpers.py`, `cli_scan.py`,
    `cli_scan_baseline.py`, `cli_scan_receipt.py`, and `cli_compare_fold.py`
    all import one or more of `policy_file`, `suppression`,
    `compatibility_evaluation_resolver`, `compatibility_evaluation_wiring`,
    `pack_application`, `contract_scoped_promotion`, `contract_evidence`,
    `contract_coverage_ledger`, `contract_context`, `contract_context_io`,
    `contract_gating`, `reclassify`, and `checker_policy` **directly**
    (function-local imports, not routed through `workflows`), and
    `frontends`'s `may_import` is `[model, workflows, report]` — `policy`
    is not in it. Classifying any of those 11 modules would turn an
    invisible (both sides currently unclassified) relationship into a
    real `dependency-direction` error, since `frontends` genuinely cannot
    reach `policy` without an intermediate `workflows` facade that does
    not exist for these call sites today.
  - **`model -> policy` (5 files, overlapping the frontends set).**
    `checker_types.py` (already classified `model`, whose `may_import` is
    `[]` — nothing at all) imports `policy_file`, `contract_relevance_types`,
    `contract_gating`, `reclassify`, and `checker_policy` directly. Same
    reasoning: `model` is the innermost layer, so any candidate it reaches
    can never be classified while that edge exists.
  - **`compare -> policy` (`checker_policy.py` specifically).** Nearly
    every classified `compare` detector module (`diff_types.py`,
    `diff_symbols.py`, `diff_filtering.py`, `diff_cxx_rules.py`, and ~30
    more) imports `ChangeKind`/`Verdict` from `checker_policy` directly,
    and `compare`'s `may_import` is `[model]` only. `checker_policy.py`'s
    role is also itself ambiguous post the ADR-061 D9 model/policy
    `ChangeKind` split (PR #902) — it now mostly re-exports
    `ChangeKind`/`HasKind` from `model.change_catalog` rather than owning
    policy logic — so both the import-graph evidence and the role
    ambiguity point the same direction: leave it unclassified.
  - **`workflows -> policy` via `compatibility_evaluation_frontend.py`
    (`pack_application.py`, `contract_context.py`).** Both import from
    `compatibility_evaluation_frontend.py`, which is already classified
    `workflows` — and `policy`'s `may_import` doesn't include `workflows`
    (imports must point strictly inward per ADR-061 D1). `pack_application.py`
    additionally imports `cli_params` (`frontends`) directly, a second,
    independent forbidden edge on the same file.

  None of these are new problems introduced by this change — they are
  pre-existing coupling this migration step's job is specifically to
  surface rather than paper over, matching the precedent PR #901 set for
  `diff_*.py` (9 of 46 candidates excluded for the identical
  `compare -> extract` shape). Untangling any of the excluded files needs
  either routing the naming `frontends`/`model`/`compare` call sites
  through the `workflows` facade, or moving the shared logic to a leaf
  module reachable from both sides — real, scoped follow-up work, not a
  ledger edit.

  Verification: `python scripts/check_architecture.py` -> 0 errors (11
  files newly classified — the 4 pre-existing `policy` entries plus the 7
  from this change); `python scripts/check_ai_readiness.py` -> 0 errors,
  warning count unaffected (no `.py` file touched); `python
  scripts/adr_status_sync.py` -> clean; `mypy abicheck/` -> clean, no
  drift (no `.py` file touched); `pytest tests/test_architecture_check.py`
  -> 40 passed; full fast unit suite green.
