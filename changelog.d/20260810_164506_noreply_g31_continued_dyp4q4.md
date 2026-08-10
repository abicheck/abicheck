<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The direct-clang vtable reconstruction's template-specialization base
  lookup (`build_specialization_index()`) no longer loses a complete
  definition to an earlier forward declaration.** An explicit
  specialization can be forward-declared (`template<> struct A<int>;`)
  before its complete definition — both share the identical `"A<int>"`
  spelling — and the index's first-registration-wins policy permanently
  kept the empty forward-decl stub whenever it was walked first, the same
  shape `_record_index()` already guards against for an ordinary record.
  Now applies the identical "a complete definition always wins" tie-break.
- **The same index no longer fabricates a mismatched spelling for a
  non-type template argument that doesn't round-trip through its raw
  JSON value.** A `bool` non-type argument's evaluated `value` (e.g. `-1`
  for `true`) does not print the same way a base reference spells it
  (`"A<true>"`), so the previous reconstruction produced an `"A<-1>"` key
  that could never match, degrading a resolvable base to unresolvable —
  or, in principle, colliding with an unrelated key. Now only trusts a
  non-type argument's raw value when the corresponding template parameter
  is confirmed (via a new whole-AST parameter-kind index) to be one of a
  small set of plain builtin integer types, matching a base's spelling
  reliably; every other non-type argument (`bool`, an enum, a pointer, ...)
  makes the specialization unindexable rather than guessed at — the same
  false-negative-over-false-positive degradation this module already uses
  throughout.
