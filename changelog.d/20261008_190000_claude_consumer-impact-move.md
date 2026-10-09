### Changed

- **Consumer-impact join moved into `workflows/` (Lane C stage 3)** — the
  ADR-057 consumer/source-graph join (`appcompat_consumer_impact.py`) is now
  `workflows/consumer_impact.py`, beside its one caller
  `workflows/consumer_scope.py`. To unblock it, the call-edge label
  constants (`CALL_KIND_*`, `RESOLUTION_*`) moved from the extractor
  `buildsource/call_graph.py` to the shared `model/graph_vocabulary.py`, and
  `buildsource/graph_impact.py` is now classified in the `compare` layer.
  Behaviour unchanged.
