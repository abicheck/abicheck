### Fixed

- **A pure template-parameter rename no longer changes a function
  template's `EntityId`.** `template<class T, T N> void f();` and
  `template<class U, U N> void f();` are the identical declaration, but
  clang's own `qualType` for the non-type parameter spells the dependent
  type literally as the type parameter's own name (`"T"`/`"U"`), which
  would otherwise fingerprint a non-semantic rename as two different
  overloads. Fixed by canonicalizing a non-type parameter's declared type
  against the preceding type parameters' names, replacing each with its
  0-based position.
