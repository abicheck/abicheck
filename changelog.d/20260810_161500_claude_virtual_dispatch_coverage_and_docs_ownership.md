### Fixed

- **`fold_virtual_dispatch_graph` stamped `extractor_passes` unconditionally,
  hiding a narrowed/degraded run**: a `dump --sources`/`collect` invocation
  scoped to `changed_paths`/`scoped_units` correctly narrows its three
  prerequisite passes (`call_graph`/`type_graph`/`override_graph`), but the
  derived virtual-dispatch pass previously reported itself as fully covered
  regardless. Now propagates `narrowed_passes`/`narrowed_scope`/
  `degraded_passes` from those prerequisites.

### Docs

- Registered `macro_graph.py`, `virtual_dispatch_graph.py`, and
  `callback_graph.py` as `fact_sources` for the `impact-analysis` topic in
  `docs/_meta/topics.yaml`, so the ownership contract tracks the code behind
  `docs/reference/source-graph-schema.md`'s macro-dependency and
  virtual-dispatch/callback sections.
