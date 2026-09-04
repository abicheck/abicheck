### Fixed

- **Two function templates differing only in a dependent return type no
  longer collapse onto one `EntityId`.** A function template's return
  type can itself depend on a template parameter (e.g.
  `template<class T> typename T::x f(T);`), so two such templates can
  share scope, leaf name, ordinary parameters, and template-parameter
  kinds while still being distinct, legally-coexisting overloads — clang
  accepts both with no redefinition error. The unmangled-template
  identity fallback now includes the canonicalized dependent return type;
  an ordinary (non-template) function's identity is unaffected, since it
  can never legally overload solely by return type.
