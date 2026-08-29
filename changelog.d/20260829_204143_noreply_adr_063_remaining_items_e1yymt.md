### Fixed

- **A member-access expression naming a member that happens to share its
  spelling with a template parameter (`S{}.N`, `((S*)0)->N`) no longer gets
  mistaken for a reference to that parameter.** The rename-blind
  substitution used to canonicalize a dependent type/parameter spelling
  against a template's own parameter names rewrote the member name `N` in
  `decltype(S{}.N)`/`decltype(((S*)0)->N)` into `type-param-0` for
  `template<int N> void f(decltype(...));`, even though clang's own type
  spelling keeps the member access verbatim — fingerprinting an unrelated,
  unused parameter's own rename as a remove+add for an otherwise-identical
  declaration.
