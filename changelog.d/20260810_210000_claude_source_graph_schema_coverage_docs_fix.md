### Docs

- Corrected `docs/reference/source-graph-schema.md`'s coverage paragraphs
  for `virtual_dispatch_graph` and `callback_graph`, which still said
  coverage lives solely at `extractor_passes[...]` — both passes' coverage
  is derived (worst-wins) from their clang-backed prerequisites and is
  mutually exclusive across `extractor_passes`/`narrowed_passes`/
  `degraded_passes`, not a single flag.
