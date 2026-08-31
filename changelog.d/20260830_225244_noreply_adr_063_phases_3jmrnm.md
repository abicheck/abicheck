### Added

- **`Change` now carries an `entity_id` field** (ADR-063 Phase 2), the
  compare-time `EntityId` of the declaration a finding is about -- the OLD
  side's when it exists, else the NEW side's, mirroring `Change.
  symbol_binding`'s own old-side convention. Populated at every
  function-level diff site across `diff_symbols.py` and its sibling
  split-out modules (return type, parameters, ref-qualifier, linkage,
  noexcept/virtual/explicit/variadic transitions, contract attributes,
  exception spec, vtable index, inline transitions, added/removed/
  deleted/visibility-changed functions, parameter renames/default-value/
  pointer-level/restrict/va_list changes, return-pointer-level changes,
  method access narrowing, deprecation and override-specifier
  transitions, hidden-friend transitions, constructor overload-ambiguity
  risk). The field is keyword-only, defaults to `None`, and is excluded
  from equality, so no existing behavior, comparison result, or report
  changes -- no consumer reads it yet (`resolve_change_identity`'s own
  migration is separate follow-on work). Variable/type/enum/platform
  detectors are not yet wired.
