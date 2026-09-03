### Fixed

- **Two function templates differing only in a pointer-to-member-function
  return type's own parameter list are no longer treated as identical.**
  The spiral-declarator detection in the clang header backend's return-type
  resolver only recognized a bare `*`/`&` pointer/reference wrapper, missing
  a pointer-to-member-function return type's own class-qualified wrapper
  (`int (C::*f(T))(int)`, spelled by clang as `int (C::*(T))(int)`) —
  falling through to the generic fallback and discarding the returned
  member function's own parameter list, the one thing distinguishing two
  such overloads. The wrapper-prefix check now recognizes any qualified
  `<class>::*` declarator alongside `*`/`&`/`&&`.
