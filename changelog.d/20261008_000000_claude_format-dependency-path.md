### Changed

- **`format_dependency_path` moved to `buildsource.source_graph_compare`** —
  the source-graph proof-path renderer (formerly the private
  `source_graph_findings._format_dependency_path`) now lives beside the
  `_label_map` it reads, in the `compare`-classified module, so the
  consumer-impact join and `internal_leak` no longer reach into the
  unclassified `source_graph_findings` for it. Behaviour unchanged.
