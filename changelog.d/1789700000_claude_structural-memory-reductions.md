### Changed

- Memory: a single-producer source-graph node/edge now materialises **one**
  attrs dictionary instead of three. `facts[0].attrs`, `resolved` and
  `attrs` held equal contents for every entity in the common one-fact
  shape; `resolve_entity_attrs`'s fast path aliases them, measured at
  -2.43 MiB and -13,851 objects per graph on a 2,771-node/7,506-edge
  release-sized graph, multiplied by the member count in a release
  fan-out. Contents, conflicts, provenance and serialization are unchanged.
- Memory: a JUnit release render no longer retains every member's full
  `AbiSnapshot`. `junit_report` read exactly four attributes off it, to
  build a symbol-name to classname map; it now receives that map as a
  compact `report.junit_inventory.JunitSymbolInventory`, projected when the
  member's comparison finishes. `--bundle-facts-out` is now the only
  consumer that keeps the whole document. Rendered JUnit documents are
  byte-identical.
- Memory: compressed snapshot/bundle-facts writes are incremental. The
  streaming writer joined every fragment and handed the whole buffer to a
  one-shot codec; `storage.incremental_encode` compresses fragment by
  fragment instead, so neither the whole JSON document nor a whole encoded
  copy is materialised. gzip output is byte-identical to the previous path,
  and zstd is too whenever the decoded size is known.
