### Changed

- **Breaking (Python API, pre-1.0):** `AbiSnapshot`'s declaration fields
  (`functions`, `variables`, `types`, `enums`, `typedefs`,
  `typedefs_qualified`, `constants`, `typedef_entity_ids`,
  `constant_entity_ids`) moved into the snapshot's semantic IR (ADR-063
  Phase 10). Read and write them as `snapshot.declarations.<kind>`; the
  constructor still accepts them as keyword inputs. `snapshot.semantic_ir`
  now always carries the declaration store — use `snapshot.canonical_ir`
  for the canonical occurrence view (or `None`). The old attribute names
  raise `AttributeError` naming the replacement. Serialized snapshots are
  unchanged.
