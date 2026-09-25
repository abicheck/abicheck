### Performance

- A graph built during a dump now shares its repeated values the way a
  decoded stored graph already did: `SourceGraphSummary` folds each new
  entity's `producer`/`confidence`/`kind` strings and scalar-only fact
  `attrs` dicts onto one object per distinct value
  (`model.graph_facts.SharedGraphValues`). On a oneDAL header graph that is
  3 `confidence` string objects instead of 42,532 and 61 `attrs` dicts
  instead of 91,835. Declaring-header strings are interned where provenance
  tags them and where snapshots are decoded.
