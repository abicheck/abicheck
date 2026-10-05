### Changed

- `compare --pattern-verdicts --surface-metrics` builds each side's surface
  graph once instead of once per stage. The graphs are shared only for the
  span of those two stages (`surface_graph.shared_surface_graphs`) and
  released afterwards, so a run with one stage holds nothing longer than
  before.
