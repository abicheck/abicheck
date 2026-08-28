### Changed

- **ADR-061 continuation**: 18 more of `buildsource/`'s 73 flat modules are
  now classified into the `extract` responsibility layer in
  `architecture/modules.yaml` -- 53 of 73 `buildsource/` files classified
  overall (up from 35). All 18 are raw fact-extraction/graph-fold/config-
  resolution modules with no `compare`-shaped or subprocess/demangle-active
  concerns: `build_config_io.py`, `build_query.py`, `compiler_record.py`,
  `extractor.py`, `extractor_manifest.py`, `graph_backends.py`,
  `inline_graph_fold.py`, `inputs_validate.py`, `pack_load.py`,
  `pattern_scan.py`, `poi.py`, `preprocessor_scan.py`, `redaction.py`,
  `snapshot_exports.py`, `source_link.py`, `source_replay.py`,
  `toolchain_bindings.py`, `toolchain_probe.py`.

  **Closes the `frontends -> buildsource` direct-import gap PR #903 left
  open**, rather than deferring it again. All 18 files were directly
  imported, at module or lazy scope, by eight `cli_*.py` modules
  (`cli_buildsource.py`, `cli_buildsource_helpers.py`,
  `cli_buildsource_merge.py`, `cli_compare_helpers.py`,
  `cli_dump_dry_run_build_query.py`, `cli_project.py`, `cli_resolve.py`,
  `cli_scan.py`) -- a real `frontends -> extract` violation the moment any
  of them is classified, per ADR-061 D9's `frontends -> workflows ->
  extract` routing rule. Rather than leave the classification blocked
  again, `abicheck/workflows/extraction.py` (the existing facade its own
  docstring already documents as "the sole operation owner" for the
  CLI-to-extract boundary) gained re-exports for all ~37 newly-needed
  names, and each of the eight CLI
  files' import sites were routed through it instead of the `buildsource`
  submodule directly -- the same "one owner per operation, patch the facade
  not the origin" pattern PR #899 already established for the parser
  modules.

  **Selection method, same discipline as PR #899/#903**: for every
  remaining unclassified `buildsource/` file, checked whether its role
  (per `AGENTS.md`'s task-routing table) and its own local imports (direct
  and transitive) would create a forbidden edge for *any* classified
  importer before touching it -- not just tentatively classifying
  everything and reacting to whatever the checker found, since several of
  the remaining files have real, load-bearing dependencies that make a
  from-scratch trial-and-narrow pass expensive to interpret correctly.
  Concretely, the files below are deliberately **not** classified in this
  pass, each for a reason already load-bearing rather than newly
  discovered:

  - `entity_identity.py`/`entity_resolver.py`, `graph_impact.py`,
    `template_graph.py`/`virtual_dispatch_graph.py`,
    `source_graph_findings.py` -- unchanged, already-established residuals
    from PR #903 (subprocess/demangle dependency, or a real cascade into
    still-`extract`-classified sibling modules that a config-only pass
    cannot resolve).
  - `crosscheck.py` -- genuinely `compare`-shaped (11 `Change`/`ChangeKind`
    construction sites) but imports the `extract`-classified
    `export_accounting.py`, which `compare`'s `may_import: [model]`
    forbids; stays unclassified rather than force an incomplete fix, the
    same reasoning PR #903 already applied to its siblings.
  - `build_evidence.py`, `fact_set.py`, `source_abi.py`, `source_graph.py`
    -- each is a shared schema/value type multiple layers read (the
    `graph_facts.py`/`model` shape), and each is imported by an already
    `compare`-classified module (`build_diff.py`, `source_diff.py`,
    `graph_reconcile.py`), so reclassifying them `extract` would trip
    `compare -> extract`. None of them can move to `model` either, though:
    `build_evidence.py` itself imports the `extract`-classified
    `comdat_groups.py`, and `source_graph.py` imports the
    still-unclassified `entity_resolver.py` (deliberately, per the
    subprocess finding above) plus `source_graph_findings.py` -- `model`'s
    `may_import: []` forbids the first outright and the second would need
    `entity_resolver.py`'s own resolution first. Left unclassified; a
    correct fix needs the same kind of dependency untangling PR #903's own
    `entity_identity.py` history already went through, not a same-pass
    reactive reclassification.
  - `evidence_report.py`, `evidence_policy.py`, `merge_support.py` --
    genuinely cross-cutting glue (evidence-side resolution/diffing/
    reporting, evidence-policy metrics, pack-merge-conflict support) that
    doesn't cleanly fit one AGENTS.md task-routing bucket; each imports a
    mix of `compare`-shaped and not-yet-classified siblings. Left for a
    dedicated design pass rather than forced into `extract` or `compare`
    on a guess.
  - `check_report.py`, `project_targets.py`, `run_plan.py`,
    `build_output.py`, `baseline_set.py` -- unchanged from PR #903's own
    note: the first three import `workflows.aggregate`/`exit_decision`
    themselves (workflows-shaped, not extract-layer regardless of who
    imports them), `build_output.py` is their shared schema, and
    `baseline_set.py` remains blocked on the now-real `extract`-classified
    `elf_metadata.py` (`storage -> extract` forbidden) exactly as
    documented after PR #899 merged.

  Verified via `scripts/check_architecture.py` (0 errors, repo-wide,
  including the `frontends -> extract` set that was the whole point of
  this pass), `scripts/check_ai_readiness.py` (0 errors, 146 warnings --
  one fewer than the 147 baseline from a large-file warning threshold
  shift, not a suppressed real issue), `mypy abicheck/` (the same
  pre-existing 17 `yaml`-stub errors, unaffected), the full
  `test_architecture_check.py`/`test_architecture_refactor.py` suite (99
  passed), a targeted run across every touched module and CLI command
  (1459 passed), and the full fast unit suite.

  One file (`cli_dump_dry_run_build_query.py`) is `debt.yaml`-tracked
  against a no-growth line-count baseline; the facade-routed import would
  have wrapped to a multi-line form that grew it past that baseline purely
  from import formatting, not from any added logic -- resolved by
  regrouping two of the three newly-facade-routed names onto an
  already-present single-line import instead of merging all three into a
  freshly wrapped block, netting zero line growth.
