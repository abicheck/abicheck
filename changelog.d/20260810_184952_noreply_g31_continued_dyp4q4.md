<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The direct-clang vtable reconstruction now resolves a base that is a
  concrete template specialization with every argument left at its
  default.** When every template argument equals its own default, the
  reconstructed spelling collapsed to an empty argument list and returned
  unresolvable, even though clang always prints an explicit, empty
  angle-bracket pair (`A<>`) on the base reference, never a bare `A`. A
  public-header-only class deriving from such a base lost its inherited
  vtable entirely, so adding a virtual method went completely undetected.
- **The same reconstruction now carries a specialization's own spelled
  qualname into a NESTED specialization it contains**, instead of
  descending with the outer specialization's bare, unqualified name. A
  base that is itself a nested specialization (`Outer<int>::A<double>`)
  previously indexed under the wrong (or nonexistent) qualname, leaving
  the base's vtable invisible in exactly the same way.
