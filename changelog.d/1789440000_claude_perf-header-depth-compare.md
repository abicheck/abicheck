### Performance

- Graph reconciliation (`buildsource/graph_reconcile.py`) now builds its
  structural-context and declaring-file indices for the added/removed
  candidates only, instead of for every node of both source graphs. Each
  restricted entry equals the whole-graph one, so reconciliation output is
  unchanged; on a 12-member header-depth release fixture the reconcile step
  drops from ~6.7 s to ~0.8 s of GIL-held time (~5% of wall).
