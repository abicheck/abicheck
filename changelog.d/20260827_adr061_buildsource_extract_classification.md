### Changed

- **ADR-061 continuation**: 36 of `buildsource/`'s 73 flat modules (plus its
  pre-existing 5) are now classified into the `extract` responsibility
  layer, 2 into `compare`, 1 into `storage`, and 1 into `model`, in
  `architecture/modules.yaml`. Config-only change, verified via
  `scripts/check_architecture.py` (0 errors) -- no source files touched, so
  behavior is unchanged.

  - `extract`: raw fact-extraction/graph-building modules
    (`header_graph.py`, `template_graph*.py`, `type_graph.py`,
    `crosscheck_base.py`, `entity_identity.py`, `clang_ast_run.py`,
    `comdat_groups.py`, and 29 siblings) with no cross-layer import
    violations.
  - `compare`: `source_diff.py`/`build_diff.py` -- both compare old/new
    surfaces and emit `Change` findings via `ChangeKind`, the `compare`
    layer's job per `AGENTS.md`'s task-routing table, not raw extraction
    (a Codex review finding on this PR's first pass, which had
    misclassified both as `extract`).
  - `storage`: `build_cache.py` -- owns content-addressed cache keys plus
    on-disk cache reads/writes, matching `storage`'s explicit "manage
    caches" ownership rather than `extract` (same review round).
  - `model`: `graph_facts.py` -- defines the shared `GraphFact`/
    `FactConflict`/`GraphNode`/`GraphEdge` schema multiple layers consume,
    not an extraction algorithm (same review round).

  `source_graph_findings.py` -- also flagged as compare-shaped by the same
  review, and it is (it emits findings, not raw facts) -- stays
  unclassified: it imports `header_graph.py`/`graph_impact.py`/
  `graph_reconcile.py` (still `extract`-classified) at module/lazy scope,
  which `compare`'s `may_import: [model]` forbids. `graph_reconcile.py` is
  itself compare-shaped (old/new node reconciliation, per AGENTS.md's
  "match old/new entities" routing) rather than a raw extractor, so
  reclassifying `source_graph_findings.py` cleanly needs that cascade
  addressed too -- left for a dedicated follow-up rather than chased under
  this same config-only pass.

  The remaining 33 `buildsource/` files stay unclassified: 30 of them are
  directly imported by `frontends`-layer `cli_*.py` modules (a pre-existing
  `frontends -> buildsource` direct-import pattern that bypasses the target
  `frontends -> workflows -> extract` routing -- ADR-061 D9's own worked
  example, `abicheck/workflows/extraction.py`), and re-classifying them
  without first adding equivalent `workflows` facade re-exports would trip
  `dependency-direction`. `check_report.py`/`run_plan.py`/`project_targets.py`
  additionally import `workflows.aggregate`/`exit_decision` themselves, so
  they aren't purely `extract` layer regardless. Each needs its own
  facade-routing follow-up, not a config-only reclassification.
