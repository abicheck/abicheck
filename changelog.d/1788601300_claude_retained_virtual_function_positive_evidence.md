### Fixed

- `SECONDARY_VTABLE_GROUP_CHANGED` (`diff_vtable_layout._is_polymorphic`)
  now also treats a retained `Function` with `is_virtual=True` owned by a
  record as unconditional proof of polymorphism, even when `vtable_fact`
  itself wasn't collected — `snapshot.functions` is a separate evidence
  stream from the class DIE's own virtual-method children
  (`RecordType.vtable`), the same independence
  `diff_types_vtable._vtable_transition_is_evidenced` already relies on.
  This matters most for a legacy direct-clang snapshot that predates
  vtable reconstruction, whose function-level `is_virtual` metadata
  survives even though `vtable`/`vtable_fact` do not. Reuses
  `diff_types_vtable._owned_virtual_signatures` (its eager
  namespace-suffix matching, hardened by several prior review rounds)
  rather than a fresh ad hoc implementation.
