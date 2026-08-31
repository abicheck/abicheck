### Changed

- **ADR-061 Phase 5 item 2 is now closed**: every internal, first-party
  production module that imported `SourceGraphSummary`/`GraphNode`/
  `GraphEdge`/`build_source_graph`/`diff_source_graph`/
  `is_public_dependency_node` (and siblings) through the legacy
  `abicheck.buildsource.source_graph` re-export facade now imports each name
  from its real owner instead — `abicheck.model.source_graph`/
  `abicheck.model.graph_facts` for the graph values, `abicheck.buildsource.
  source_graph_build`/`source_graph_build_source_abi` for construction,
  `source_graph_compare` for comparison, and `source_graph_query` for the
  shared node/edge-classification predicates. A small, explained set of
  callers stays on the facade where a real architectural constraint left no
  clean path: three `debt-no-growth`-tracked files at their adoption-baseline
  line count (`buildsource/graph_reconcile.py`, `buildsource/template_graph.py`,
  `internal_leak.py`), and three files whose own `architecture/modules.yaml`
  classification (`extract`, or `frontends`) does not permit importing the
  real owner's layer directly (`buildsource/poi.py`, `cli_buildsource_helpers.py`,
  `cli_buildsource_merge.py`). `buildsource/source_graph.py` itself is
  untouched — it remains the facade for external callers and the exceptions
  above.

### Notes

- Pure import-path rewrite: no function/class body changed. `mypy abicheck/`,
  `ruff check abicheck/ tests/`, `python scripts/check_architecture.py`, and
  `python scripts/check_ai_readiness.py` are all clean (identical error/
  warning counts to before this change); the fast unit suite passes with the
  same test count as before.
