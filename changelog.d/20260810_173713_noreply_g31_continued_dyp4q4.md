<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The direct-clang vtable reconstruction no longer fabricates a new
  vtable slot for an explicitly-marked override on a class with an
  unresolvable base.** A base that this backend can't resolve (a `bool`/
  enum/other untrusted non-type template argument, or a template-dependent
  base in an unparsed header) is invisible to the reconstruction — but an
  own member carrying an explicit `virtual`/`override` keyword was still
  unconditionally treated as a brand-new slot, since nothing recognized it
  as a possible override of something in the invisible base. An old
  snapshot with only the unresolvable base read as an empty vtable; a new
  snapshot adding just the explicit override then read as gaining one new
  slot — a real `VPTR_INTRODUCED`/`TYPE_VTABLE_CHANGED` false positive.
  Fixed by suppressing any new (non-candidate-matching) own slot on a
  class with at least one unresolved base — ambiguous whether such a
  member is a genuine addition or an invisible override, so it now
  degrades to the same accepted false negative this backend's
  unresolvable-base handling already uses elsewhere.
- **The same reconstruction now resolves a base using a template default
  that depends on an earlier parameter** (`template <class T, class U =
  T> struct A; struct D : A<double> {...};`). The default's own reported
  spelling is the literal, unsubstituted parameter name (`"T"`), which
  never textually equals the real resolved argument (`"double"`), leaving
  the base unresolvable. A default that exactly names an earlier
  parameter is now substituted with that parameter's own resolved
  argument before comparing; anything more complex (a default only
  partially referencing an earlier parameter) is conservatively left
  unsubstituted.
