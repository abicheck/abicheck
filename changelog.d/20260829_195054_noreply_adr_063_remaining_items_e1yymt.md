### Fixed

- **An ordinary member function nested inside a class template no longer
  changes identity on a pure rename of the ENCLOSING class template's own
  parameter.** `entity_id_for_function`'s ordinary-parameter
  canonicalization only ever saw a directly-templated function's own
  parameter names, not a class template's — so `template<class T> struct
  A { void f(T); };` renamed to `template<class U> struct A { void
  f(U); };` (the identical declaration) fingerprinted as a remove+add,
  since `f` is never itself a function template. The clang header-AST
  walk now accumulates an enclosing class template's (and a partial
  specialization's) own parameter names alongside any function
  template's, so every declaration in its pattern body canonicalizes
  against them.
