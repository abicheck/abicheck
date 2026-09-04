### Fixed

- **The typedef cutover's fidelity gate now also validates identity, not
  just names and values.** `typedef_index_pair` accepted the real
  `SemanticIR`-backed index whenever its display-name key sets and
  underlying-type spellings matched the legacy alias maps, but never
  checked whether the IR's own resolved `EntityId` per alias agreed with
  what the legacy adapter would independently assign that same alias via
  the `typedef_entity_ids` sidecar. A loaded or Python-constructed
  snapshot can carry a real IR resolving an alias under one identity while
  its sidecar names a different, differently-scoped identity that happens
  to render back to the identical alias text — names and values matching
  while identity silently disagreed. Picking the IR path there stamped a
  different `entity_id` on the emitted finding than the pre-cutover
  detector would have for identical data, silently changing which stored
  `entity:`-alias suppression rules match. The gate now also requires each
  alias's IR-resolved identity to equal the identity a fresh legacy
  projection assigns it, falling back to the adapter on any disagreement
  (Codex review on PR #1041).
