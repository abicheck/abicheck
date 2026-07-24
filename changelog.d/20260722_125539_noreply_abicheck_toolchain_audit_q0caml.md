### Fixed

- **The C++20 dialect detector's requires-clause branch now requires
  template context, matching the `concept` branch's existing design.** A
  plain pre-C++20 declaration using "requires" as an ordinary type/variable
  name (`struct requires {}; requires value;`) had the identical bare
  `requires\s+\w` shape as a genuine requires-clause, but that branch
  previously had no declarator check at all. A genuine clause is always
  preceded by its own `template<...>` header; the declaration case is not.
- **The C++20 dialect detector now recognizes constrained template
  parameters using a standard-library concept** (the abbreviated
  `template <std::integral T> void f(T);` form), which the module's own
  docstring already described as in-scope but no pattern ever matched.
  Deliberately scoped to the fixed, well-known set of concepts in
  `<concepts>`/`<iterator>`/`<ranges>` rather than any bare or qualified
  identifier, since an arbitrary identifier there is routinely a valid
  pre-C++20 non-type template parameter's type.
