### Fixed

- **The clang header backend now recovers a function's TRAILING return
  type (`auto f(T) -> typename T::x`) instead of the bare `auto`
  placeholder.** `_return_type` used to return only the leading spelling
  of a function's `qualType`, which clang spells as the literal string
  `"auto"` for a trailing-return-type function — collapsing two legal,
  coexisting overloads that differ only in their trailing return type
  (`-> typename T::x` vs. `-> typename T::y`) onto the identical
  `return_type` and, for an uninstantiated template with no mangled
  name, the identical `EntityId`. The real return type is now recovered
  from after the parameter list's trailing `->`, when present.
