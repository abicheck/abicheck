### Fixed

- **A source graph's entity-resolver conflicts no longer depend on node
  insertion order** — `SourceGraphSummary.resolve_entities()` now resolves
  nodes in id order, the order `finalize()` writes them. When two node ids
  shared one canonical identity, the representative id and each conflict's
  id order followed the producer's insertion order, which can differ between
  processes. A reloaded graph, whose resolver is rebuilt from the sorted
  rows, could therefore disagree with the graph that was saved.
