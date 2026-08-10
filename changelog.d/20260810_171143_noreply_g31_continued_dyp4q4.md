<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The direct-clang vtable reconstruction no longer misreads a C++14-and-
  earlier dynamic exception specification as part of override identity.**
  A base `virtual void f() throw(int);` overridden by a derived `void f()
  throw() override;` — a legal narrowing — previously compared as a
  different signature (only `noexcept` was stripped from the qualifier
  tail, not `throw(...)`), so the override appended a spurious second
  vtable slot instead of replacing the inherited one.
- **The same reconstruction now resolves a base that omits a template
  argument matching its own default.** A specialization always carries a
  template argument for every parameter, including one a base reference
  omitted because it equals its default (`template <class T, class U =
  int> struct A; struct D : A<double> {...};` records arguments for both
  `T` and `U`), so joining all of them unconditionally produced a spelling
  the referring site's own (defaults-collapsed) spelling never matched,
  leaving the base unresolvable. A new whole-AST pass now records each
  template parameter's own default (type parameters only), and trailing
  arguments matching it are dropped before building the index key —
  matching clang's own canonical spelling for both an omitted default and
  one explicitly repeated with the same value.
- **`dumper_clang.py`'s specialization-owner qualification (used to keep
  `owner_class_of()` resolving correctly) now agrees with the base-lookup
  index on which non-type/defaulted arguments to trust**, instead of using
  no template-parameter context at all — a `struct D : A<3> {...}` or a
  defaulted-argument base could otherwise still produce a false
  `TYPE_VTABLE_CHANGED` even after its vtable itself resolved correctly.
- **`_base_lookup_index()` is now memoized** instead of rebuilding the
  merged record + specialization index on every call — `_build_record`
  calls it once per record in the translation unit.
