### Fixed

- **`fold_callback_graph` let a degraded/missing/narrowed `call_graph` pass
  masquerade as full callback-graph coverage**: Part A's `CALLBACK_MAY_INVOKE`
  join reads `call_graph`'s own already-folded function-pointer-kind
  `DECL_CALLS_DECL` edges, so a degraded or never-run `call_graph` pass means
  a real dispatch target can be silently absent even when this pass's own
  clang run (Part B) examined the whole compile DB cleanly. The coverage
  stamp now propagates `call_graph`'s own state (worst-wins: degraded/missing
  > narrowed > full), mirroring the propagation `fold_virtual_dispatch_graph`
  already applies for its own three prerequisites.

### Docs

- Registered `abicheck/buildsource/graph_facts.py` as a fact source for the
  `impact-analysis` topic in `docs/_meta/topics.yaml` — the shared node/edge
  kind and confidence-tier vocabulary the macro/virtual-dispatch/callback
  graph modules all build on was missing from that topic's registry.
