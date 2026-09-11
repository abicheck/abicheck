### Fixed

- **Internal: made the depth-projection coverage-row insertion order
  deterministic.** `policy/depth_projection.py`'s `_mark_layers_not_collected`
  inserted a fresh `NOT_COLLECTED` row for a layer with no prior coverage row
  by iterating a `frozenset`, whose order depends on the process's
  `PYTHONHASHSEED` — a coverage-free pack's L4/L5 rows could come out in
  either order across otherwise-identical runs, making the human coverage
  table and serialized `layer_coverage` array non-reproducible. Missing
  layers are now appended in a fixed evidence-ladder order derived from
  `DataLayer`'s own declaration order.
