### Fixed

- **An explicitly globally-qualified name (`::T::X`) no longer gets
  mistaken for a reference to a template parameter merely because it
  collides in spelling.** The rename-blind substitution used to
  canonicalize a dependent type/parameter spelling against a template's
  own parameter names rewrote `::T::X` into `::type-param-0::X` for
  `template<class T> void f(::T::X);`, even though clang's own type
  spelling proves `::T` names the global namespace, not the parameter —
  fingerprinting an unrelated, unused parameter's own rename as a
  remove+add for an otherwise-identical declaration.
