### Fixed

- **`TYPE_VTABLE_CHANGED` no longer reports BREAKING for a type whose layout
  evidence is itself unverifiable.** `_vtable_transition_is_evidenced`
  (`diff_types.py`) and the `LAYOUT_UNVERIFIABLE` detector
  (`diff_layout.py`) both key off the same "one side has real layout
  evidence, the other has none" condition but previously reached opposite
  severities from it, so a type with an evidence gap could carry a
  BREAKING `TYPE_VTABLE_CHANGED` right alongside a RISK
  `LAYOUT_UNVERIFIABLE` for the identical missing evidence — reproducible
  with zero real ABI change (comparing a binary against a dump of itself).
  A new post-processing step,
  `degrade_vtable_changed_for_unverifiable_layout`, demotes a
  `TYPE_VTABLE_CHANGED` finding to `COMPATIBLE_WITH_RISK` whenever
  `LAYOUT_UNVERIFIABLE` also fired for the same type.

