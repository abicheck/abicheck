### Fixed

- **`_snapshot_abi_snapshot`'s `dependency_info` copy now decouples each
  dependency-record entry, not just the outer lists** — the previous fix
  gave `dependency_info.nodes`/`.edges`/`.unresolved`/`.missing_symbols`
  fresh outer containers, but each `dict` entry inside them was still
  shared with the caller. Mutating `old.dependency_info.nodes[0]["soname"]`
  after `build_report_envelope()` could still make one projection (e.g.
  Markdown) disagree with another already rendered from the same envelope.
  Each entry now gets its own `copy.deepcopy` — still far cheaper than
  deep-copying the whole snapshot, since a dependency record is a small,
  JSON-shaped dict, not a large object graph.
