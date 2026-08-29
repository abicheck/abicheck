### Changed

- **ADR-061 Phase 5 item 2, first slice: split the L5 source-graph
  *values* out of `buildsource/source_graph.py` into
  `abicheck/model/source_graph.py`.** Item 2 ("separate source-graph
  values, construction, and comparison") had no prior work; this PR moves
  the value half only — `SourceGraphSummary` (the ADR-031 D7 compact
  graph container, with its `add_node`/`add_edge`/`indexes`/
  `compute_graph_id`/`resolve_entities`/`finalize`/`to_dict`/`from_dict`
  methods unchanged), `GraphSummaryDiff` (the structural-diff result
  shape), the node-id constructors (`_source_node_id`, `_header_node_id`,
  `_option_node_id`, `_vtable_node_id`, `_symbol_node_id`, `_macro_node_id`,
  `_debug_type_node_id`, `_object_node_id`, `_static_library_node_id`,
  `_version_script_node_id`, `_type_node_kind`, `function_decl_identity`),
  and the schema vocabulary (`NODE_KINDS`, `EDGE_KINDS`,
  `DEPENDENCY_EDGE_KINDS`, `SOURCE_GRAPH_VERSION`, `EVIDENCE_TIER_L5`,
  `_FULL_WALK_SOURCE_EDGES_PRODUCER`). Construction (`build_source_graph`
  and its `_fold_*`/`_augment_*` helpers) and comparison
  (`diff_source_graph`, `localize_symbol`) stay in
  `buildsource/source_graph.py` — a separate, not-yet-attempted follow-up
  slice of the same item, per the same "one entity class per module,
  never a whole-file rewrite in one pass" discipline Phase 5 item 1's own
  first slice used.

  `buildsource/source_graph.py` imports and re-exports every moved name
  (`X as X`, matching the file's own pre-existing `graph_facts.py`
  re-export convention for mypy's `--no-implicit-reexport`), so all 77
  existing callers importing from `abicheck.buildsource.source_graph`
  keep resolving unchanged. `buildsource/source_graph.py` drops from 2000
  to 1352 lines; its `architecture/debt.yaml` no-growth baseline is
  lowered to match (2000 → 1352) so the ledger tracks the file's real
  size instead of masking the improvement.

  `_conf_from_build` (a private `Confidence -> str` mapper) stayed
  behind rather than moving: it's the one piece of the moved code that
  needed `buildsource/build_evidence.py`'s `Confidence` enum, and
  `build_evidence.py` itself transitively imports `comdat_groups.py`
  (`extract`-classified) — classifying `build_evidence.py` `model` to
  satisfy that one function would have created a real `model -> extract`
  cycle finding, caught by `check_architecture.py` before this landed
  (confirmed by re-running the check, not assumed). `entity_resolver.py`
  had no such transitive coupling — its own imports
  (`entity_identity.py`, `graph_facts.py`) don't matter for direction
  purposes while it stays physically flat — so it's now classified
  `model` in `architecture/modules.yaml`'s `legacy_paths` (the
  virtual-classification-before-physical-move pattern this ADR's Phase 3
  and Phase 4 sections already established for other leaf modules), since
  `SourceGraphSummary.entity_resolver`'s field type needs it.

  `abicheck/model/AGENTS.md`'s routing table gained one row (an L5
  source-graph value/node-id/vocabulary change routes to
  `model/source_graph.py`). `python scripts/check_architecture.py` stays
  at 0 errors. Pure code-motion — no renderer, detector, or
  classification changed; `tests/test_source_graph.py`,
  `tests/test_consumer_graph.py`, and `tests/test_use_cases.py` (which
  import `NODE_KINDS`/`EDGE_KINDS` from the flat re-export) pass
  unchanged, and the full fast suite was re-run clean.
