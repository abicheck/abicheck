### Changed

- **ADR-061 Phase 5 item 2 is now closed**: every internal, first-party
  production module that imported `SourceGraphSummary`/`GraphNode`/
  `GraphEdge`/`build_source_graph`/`diff_source_graph`/
  `is_public_dependency_node` (and siblings) through the legacy
  `abicheck.buildsource.source_graph` re-export facade now imports each name
  from its real owner instead — `abicheck.model.source_graph`/
  `abicheck.model.graph_facts` for the graph values, `abicheck.buildsource.
  source_graph_build`/`source_graph_build_source_abi` for construction,
  `source_graph_compare` for comparison, and `abicheck.model.
  source_graph_query` for the shared node/edge-classification predicates.
  Two follow-up commits in this same PR closed the remaining six exceptions
  this fragment originally recorded as blocked (`buildsource/
  graph_reconcile.py`/`internal_leak.py` via an explicit debt-baseline bump;
  `buildsource/poi.py`/`cli_buildsource_helpers.py`/`cli_buildsource_merge.py`
  via a physical relocation of the shared predicates into `model/
  source_graph_query.py` plus a sanctioned `workflows` indirection;
  `buildsource/template_graph.py` last, via a split into
  `template_graph_fold.py`) — no internal exception remains.
  `buildsource/source_graph.py` itself is untouched — it remains the facade
  for external callers only.

### Notes

- Pure import-path rewrite: no function/class body changed. `mypy abicheck/`,
  `ruff check abicheck/ tests/`, `python scripts/check_architecture.py`, and
  `python scripts/check_ai_readiness.py` are all clean (identical error/
  warning counts to before this change); the fast unit suite passes with the
  same test count as before.
