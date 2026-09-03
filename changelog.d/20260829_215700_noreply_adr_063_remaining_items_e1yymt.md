### Fixed

- **A member function template's own non-type parameter no longer
  changes identity when only an ENCLOSING class template's parameter is
  renamed.** `template<class T> struct A { template<T N> void f(); };`
  is valid C++ — the member template's own non-type parameter `N` can
  legally reference the enclosing class template's parameter `T` — but
  `function_template_param_kinds` was never seeded with the enclosing
  class's own parameter names, only its own, so renaming the enclosing
  parameter to `U` produced `nontype:T` vs. `nontype:U` for the
  identical declaration, fingerprinting it as two different overloads.
