### Changed

- ADR-061 continuation: classified `export_surface.py` into the `policy`
  responsibility layer in `architecture/modules.yaml` — pure data-only
  ledger change, `check_architecture.py` reports 0 errors both before and
  after. `export_surface.py`'s own imports resolve only to `model`/
  `compare` (already-classified) — the exact set `policy`'s
  `may_import: [model, compare]` allows.

  This branch originally proposed 7 additions
  (`compatibility_evaluation_config.py`, `contract_evaluation.py`,
  `contract_evidence_collect.py`, `contract_pipeline.py`,
  `export_surface.py`, `pattern_verdicts.py`, `suppression_yaml.py`), but
  the actual number this PR's own diff nets against the merged tree turned
  out to be smaller twice over, on review (Codex, two separate rounds):

  - **Four of the seven were already covered elsewhere by the time this
    PR merged `main` back in.** `compatibility_evaluation_config.py`,
    `contract_evaluation.py`, and `contract_evidence_collect.py` had
    already been independently classified into `policy` by other,
    separately-merged sibling PRs (so this PR's own addition of them is a
    no-op against the merged tree — same target, same entry, not a second
    ledger row); `suppression_yaml.py` likewise — a sibling PR had already
    added it to `policy.legacy_paths` before this branch's own commit
    re-added the identical entry. A fifth, `contract_pipeline.py`, had
    been classified `workflows` instead by a different sibling PR (see
    `docs/contribute/adr/061-responsibility-package-architecture.md` and
    that PR's own reasoning) — kept there, not reclassified into `policy`,
    per that already-settled decision.
  - **A sixth, `pattern_verdicts.py`, does not belong in `policy` at all**
    (Codex review, second round, confirmed by reading the module
    directly). `apply_pattern_verdicts()` is not solely policy logic: its
    own helpers `_emit_lost_invariants()` and `_emit_new_antipatterns()`
    (`abicheck/pattern_verdicts.py:282-366`, `:243-279`) compare the *old*
    and *new* snapshots directly (`old_idioms`/`new_graph`, `old_aps`/
    `new_aps`) and construct brand-new `Change(...)` objects for facts
    those helpers identify from that comparison
    (`OPAQUE_INVARIANT_BROKEN`/`HANDLE_TYPE_CHANGED`, a newly-introduced
    anti-pattern) — that is `compare/`-shaped raw-change identification,
    not `policy/`-shaped relevance/classification/severity work, per this
    repo's own task-routing table. Only the module's third phase,
    `_modulate_change()` (demoting/annotating a `Change` a detector already
    produced), is genuinely policy-shaped. Reverted the `policy.legacy_paths`
    entry; `pattern_verdicts.py` joins the "deliberately left unclassified"
    set below, for this reason specifically — a role mismatch, not a
    forbidden-edge one. Splitting the raw-change detectors into `compare/`
    so the remainder can be classified `policy` is real, scoped follow-up
    work, not a same-PR fix.

  So this PR's own genuine net-new contribution is **one** module,
  `export_surface.py`. `architecture/modules.yaml`'s `policy.legacy_paths`
  holds **9 entries** total on the merged tree: `export_surface.py` (this
  PR), `analysis_assurance.py` (pre-existing before this PR), and
  `compatibility_evaluation_config.py`/`compatibility_evaluation_packs.py`/
  `contract_evaluation.py`/`contract_evidence_collect.py`/`semver.py`/
  `suppression_yaml.py`/`surface.py` (added by sibling PRs merged in ahead
  of or alongside this one).

  A much larger candidate set was investigated
  (`policy_file.py`, `suppression.py`, `contract_relevance_types.py`,
  `compatibility_evaluation_resolver.py`, `compatibility_evaluation_wiring.py`,
  `pack_application.py`, `contract_scoped_promotion.py`,
  `contract_coverage_ledger.py`, `contract_context.py`,
  `contract_context_io.py`, `contract_replay.py`, `contract_gating.py`,
  `reclassify.py`, `checker_policy.py` — 14 modules). By the time this PR
  merged the latest `main` back in, **six of those fourteen had already
  been classified elsewhere by separately-merged sibling PRs** (Codex
  review, third round — re-verified against the actual merged tree rather
  than this fragment's own earlier text): `contract_relevance_types.py` is
  now `model`; `compatibility_evaluation_wiring.py`, `contract_scoped_
  promotion.py`, `contract_context.py`, `contract_context_io.py`, and
  `contract_replay.py` are now `workflows`. The remaining **eight** —
  `policy_file.py`, `suppression.py`, `compatibility_evaluation_resolver.py`,
  `pack_application.py`, `contract_coverage_ledger.py`, `contract_gating.py`,
  `reclassify.py`, `checker_policy.py`, plus `pattern_verdicts.py` from
  above (nine, counting it) — remain deliberately unclassified: not
  because their *role* doesn't fit policy (most are exactly the "decide
  relevance/suppression/classification/severity/gating" family the
  task-routing table describes, and `pattern_verdicts.py`'s own
  `_modulate_change()` phase is genuinely policy-shaped too), but because
  classifying the eight surfaces real, pre-existing forbidden edges the
  architecture gate would otherwise catch, and `pattern_verdicts.py` is a
  role mismatch as explained above:

  - **`frontends -> policy` (all 8 of the remaining candidates).** The 11
    files named next are the `frontends`-side *importers* hitting this
    edge, not the excluded candidates themselves: `cli_params.py`,
    `cli_compare_helpers.py`, `cli_compare_receipt.py`,
    `cli_compare_release.py`, `cli_compare_release_helpers.py`,
    `cli_helpers_compare.py`, `cli_buildsource_helpers.py`, `cli_scan.py`,
    `cli_scan_baseline.py`, `cli_scan_receipt.py`, and `cli_compare_fold.py`
    each still import one or more of `policy_file`, `suppression`,
    `compatibility_evaluation_resolver`, `pack_application`,
    `contract_coverage_ledger`, `contract_gating`, `reclassify`, and
    `checker_policy` **directly** (module-level or function-local imports,
    not routed through `workflows`; re-verified with a direct grep against
    the current tree), and `frontends`'s `may_import` is
    `[model, workflows, report]` — `policy` is not in it. The six
    now-classified modules no longer contribute to this edge at all: four
    of them (`compatibility_evaluation_wiring.py`, `contract_scoped_
    promotion.py`, `contract_context.py`, `contract_context_io.py`) landed
    in `workflows`, which `frontends` *may* import, so their own former
    `frontends -> policy` risk is simply gone, not merely relocated.
  - **`model -> policy` (4 files, overlapping the frontends set).**
    `checker_types.py` (already classified `model`, whose `may_import` is
    `[]` — nothing at all) imports `policy_file`, `contract_gating`,
    `reclassify`, and `checker_policy` directly (re-verified: module-level
    for the first, function-local for the latter three). This bullet
    previously also named `contract_relevance_types.py`, but that module
    is now classified `model` itself — `checker_types.py` importing it is
    an ordinary same-layer import, not a `model -> policy` edge, so it
    drops out of this list entirely rather than being "resolved by
    reclassification" the way the frontends-set members were.
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
  - **`policy -> workflows` via `compatibility_evaluation_frontend.py`/
    `compatibility_evaluation_wiring.py` (`pack_application.py`).**
    `pack_application.py` imports both directly (re-verified) — the second
    of those two targets is itself a result of this same round's
    reclassification (`compatibility_evaluation_wiring.py` moved to
    `workflows`), so this is now a two-target instance of the same
    forbidden edge, not a one-target one. `pack_application.py`
    additionally imports `cli_params` (`frontends`) directly, a third,
    independent forbidden edge on the same file. This bullet previously
    also named `contract_context.py`, but that module is now classified
    `workflows` itself, so it's no longer a *candidate* blocked by this
    edge — it's simply resolved.

  None of these are new problems introduced by this change — they are
  pre-existing coupling this migration step's job is specifically to
  surface rather than paper over, matching the precedent PR #901 set for
  `diff_*.py` (9 of 46 candidates excluded for the identical
  `compare -> extract` shape). Untangling any of the excluded files needs
  either routing the naming `frontends`/`model`/`compare` call sites
  through the `workflows` facade, or moving the shared logic to a leaf
  module reachable from both sides (or, for `pattern_verdicts.py`
  specifically, splitting its raw-change detectors into `compare/` first)
  — real, scoped follow-up work, not a ledger edit.

  Verification: `python scripts/check_architecture.py` -> 0 errors (9
  total `policy.legacy_paths` entries — 8 already present via other,
  separately merged sibling PRs plus 1 newly added by this PR's own diff);
  `python scripts/check_ai_readiness.py` -> 0 errors, warning count
  unaffected (no `.py` file touched); `python scripts/adr_status_sync.py`
  -> clean; `mypy abicheck/` -> clean, no drift (no `.py` file touched);
  `pytest tests/test_architecture_check.py` -> 40 passed; full fast unit
  suite green.
