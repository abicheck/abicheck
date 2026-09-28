### Performance

- Graph reconciliation (`buildsource/graph_reconcile.py`) now builds its
  structural-context and declaring-file indices for the added/removed
  candidates only, instead of for every node of both source graphs. Each
  restricted entry equals the whole-graph one, so reconciliation output is
  unchanged; on a 12-member header-depth release fixture the reconcile step
  drops from ~6.7 s to ~0.8 s of GIL-held time (~5% of wall).
- Ownership classification (`extract/ownership.py`) now resolves each
  declaring-file path once per resolved rule set -- root matching, the
  system-header check and private-header globbing -- instead of once per
  declaration. The cache lives on the `ResolvedOwnershipRules` object and is
  excluded from its equality and hash. On the same fixture `classify` drops
  from ~8.2 s to ~0.8 s of GIL-held time.
