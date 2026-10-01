### Removed

- Removed the `abicheck.buildsource.graph_facts` back-compat facade (ADR-063 Phase 10). Import `GraphNode`, `GraphEdge`, the fact-merge helpers and the graph vocabulary from `abicheck.model.graph_facts` instead; the identity helpers now live in `abicheck.model.graph_identity`.
