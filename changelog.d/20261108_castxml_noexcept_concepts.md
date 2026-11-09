### Fixed

- **castxml no longer reads a `noexcept` or C++20 constraint edit as `NO_CHANGE`.**
  castxml writes neither a function's `noexcept` nor an uninstantiated
  template, so adding `noexcept` to a virtual or a `requires`-clause to a
  header-only template compared clean. The castxml backend now reads both
  from the clang AST of the same header (cached; extractor generation 46).
  Without clang, the affected functions record `noexcept` as not observed:
  the comparison declines them and `analysis_assurance` reads `partial`,
  naming "C++ noexcept exception specifications", instead of `complete`.
  A pre-46 castxml baseline's `noexcept` values load as not observed;
  re-dump it.
- **Adding `noexcept` to a virtual method is a source break.** An overrider
  without `noexcept` no longer compiles; both header backends now report the
  new `func_virtual_noexcept_added` (`API_BREAK`) beside
  `func_noexcept_added`.
- **Constraint tightenings on a class template or an abbreviated template are
  reported.** A `concept_tightened` on an uninstantiated class template was
  scoped out as a non-public type, and `std::integral auto` did not pair with
  the unconstrained `auto`. The concept dimension is also named for an
  unmodelled side when either side's headers constrain a template, and the
  header scan now recognises type-constraints and constrained `auto`.
