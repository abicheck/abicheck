### Changed

- **`abicheck.buildsource.source_graph_query` is now classified `compare`**
  (ADR-061 Phase 5 item 2's last residual). The module's shared node/edge
  classification predicates (`is_public_dependency_node`,
  `is_internal_dependency_node`, `is_consumer_compiled_node`,
  `looks_like_system_name`, etc.) were left deliberately unclassified when
  the rest of `source_graph.py` was split — they classify structure on an
  already-built graph rather than deciding policy, which fits `compare`'s
  "match/identify a raw change" role better than `policy`'s
  "decide relevance/suppression/severity" one, and `policy -> compare` is an
  allowed edge for the module's two `policy`-classified callers
  (`surface.py`, `post_processing_reachability.py`).

### Notes

- No behavior or import-path change: this is `architecture/modules.yaml`
  bookkeeping only. `python scripts/check_architecture.py` reports 0
  findings, verified across eight explicit `PYTHONHASHSEED` values against
  the AI-readiness `import-cycle-growth` gate's own order-dependent cycle
  enumeration.
