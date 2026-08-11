### Fixed

- **`LAYOUT_UNVERIFIABLE` no longer duplicates a `TYPE_VTABLE_CHANGED`
  already reporting the identical evidence gap for the same type.**
  `_vtable_transition_is_evidenced` (`diff_types.py`) and the
  `LAYOUT_UNVERIFIABLE` detector (`diff_layout.py`) both key off the same
  "one side has real layout evidence, the other has none" condition —
  `TYPE_VTABLE_CHANGED` correctly stays BREAKING for it (an unknown size
  corroborates nothing but also refutes nothing, so the finding must be
  kept), while `LAYOUT_UNVERIFIABLE` reports the identical gap as calm,
  non-escalating RISK. Landing on the same type in the same report read as
  two detectors disagreeing about one piece of evidence — reproducible
  with zero real ABI change (comparing a binary against a dump of itself).
  A new post-processing step,
  `suppress_layout_unverifiable_covered_by_vtable_changed`, folds the
  redundant `LAYOUT_UNVERIFIABLE` advisory into `redundant_changes`
  whenever a `TYPE_VTABLE_CHANGED` already reports the same gap for the
  exact same type — `TYPE_VTABLE_CHANGED`'s own severity is never touched,
  so a genuine ABI break can never be masked by this change.
