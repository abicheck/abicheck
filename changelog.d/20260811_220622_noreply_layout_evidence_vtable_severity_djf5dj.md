### Fixed

- **`LAYOUT_UNVERIFIABLE` no longer reads as contradicting a
  `TYPE_VTABLE_CHANGED` reporting the identical evidence gap for the same
  type.** `_vtable_transition_is_evidenced` (`diff_types.py`) and the
  `LAYOUT_UNVERIFIABLE` detector (`diff_layout.py`) both key off the same
  "one side has real layout evidence, the other has none" condition —
  `TYPE_VTABLE_CHANGED` correctly stays BREAKING for it (an unknown size
  corroborates nothing but also refutes nothing, so the finding must be
  kept), while `LAYOUT_UNVERIFIABLE` reports the identical gap as calm,
  non-escalating RISK. Landing on the same type in the same report read as
  two detectors disagreeing about one piece of evidence — reproducible
  with zero real ABI change (comparing a binary against a dump of itself).
  A new post-processing step,
  `annotate_layout_unverifiable_covered_by_vtable_changed`, cross-references
  the two: when a `TYPE_VTABLE_CHANGED` already reports the same gap for
  the exact same type, the redundant `LAYOUT_UNVERIFIABLE` finding's
  `correlated_change_kind` is set to `type_vtable_changed`. Both findings
  stay fully reported in `changes` and independently scored exactly as
  before — nothing is removed or hidden, so every consumer (the legacy
  verdict, a `PolicyFile` override, a severity-scheme exit code) sees the
  same findings it always did, plus the cross-reference. The cross-reference
  is now also rendered in the default markdown, HTML, and JUnit reports
  (as a "See also" note / `abicheck.correlated_change_kind` JUnit property)
  — previously only the JSON and SARIF reports surfaced it.
