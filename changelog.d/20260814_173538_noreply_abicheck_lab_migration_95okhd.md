### Fixed

- **Fixed a false `func_removed`/`func_added` pair on an unchanged
  constructor or destructor when comparing a baseline snapshot produced
  before PR #582 against one produced after it.** `dumper_castxml.py`'s
  synthetic ctor/dtor snapshot key changed from a bare class name
  (`__abicheck_ctor__Foo()`) to a namespace-qualified one
  (`__abicheck_ctor__ns::Foo()`) to avoid cross-namespace key collisions —
  correct going forward, but it left an old-format baseline and a
  new-format snapshot of the same unchanged declaration reporting a
  spurious BREAKING removal+addition. `finding_identity_ctor_dtor.py` adds
  a narrowly-scoped, ambiguity-safe fallback tier to `diff_symbols.py`'s
  function matching that recognizes abicheck's own synthetic-key format
  drift (never a real mangled symbol) and merges such a pair only when
  exactly one unmatched candidate exists on each side with an identical
  canonicalized (namespace-stripped owner, ctor/dtor kind, parameter-type)
  form, AND the pair is a demonstrable legacy-bare/current-qualified
  format drift — exactly one side's raw owner is namespace-unqualified,
  the other qualified with a matching bare tail. Two already-qualified
  owners (e.g. `ns1::Foo` vs `ns2::Foo`) are never merged, since that
  would hide a genuine breaking namespace move as `NO_CHANGE`. The
  resolved reconciliation is also now exposed
  (`iter_matched_function_pairs`) to every other per-pair function
  detector that does its own key join — inline transitions, method-access
  narrowing, parameter default/rename/pointer-level changes,
  `[[deprecated]]` transitions, and `restrict`/`va_list` qualifier
  changes — so a genuine, unrelated property change on the same
  reconciled ctor/dtor pair (e.g. going public to private) is still
  correctly reported instead of silently disappearing.
