### Changed

- **ADR-061 continuation**: 39 of `buildsource/`'s 73 flat modules (plus its
  pre-existing 5) are now classified into the `extract` responsibility
  layer in `architecture/modules.yaml` -- raw fact-extraction/graph-building
  modules (`build_diff.py`, `header_graph.py`, `template_graph*.py`,
  `type_graph.py`, `crosscheck_base.py`, `entity_identity.py`,
  `clang_ast_run.py`, `comdat_groups.py`, and 31 siblings) with no
  cross-layer import violations. Config-only change, verified via
  `scripts/check_architecture.py` (0 errors) -- no source files touched, so
  behavior is unchanged.

  The remaining 34 `buildsource/` files stay unclassified: 30 of them are
  directly imported by `frontends`-layer `cli_*.py` modules (a pre-existing
  `frontends -> buildsource` direct-import pattern that bypasses the target
  `frontends -> workflows -> extract` routing -- ADR-061 D9's own worked
  example, `abicheck/workflows/extraction.py`), and re-classifying them
  without first adding equivalent `workflows` facade re-exports would trip
  `dependency-direction`. `check_report.py`/`run_plan.py`/`project_targets.py`
  additionally import `workflows.aggregate`/`exit_decision` themselves, so
  they aren't purely `extract` layer regardless. Each needs its own
  facade-routing follow-up, not a config-only reclassification.
