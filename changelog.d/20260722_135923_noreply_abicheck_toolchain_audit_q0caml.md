### Fixed

- **The C++20 dialect detector now recognizes a trailing requires-clause
  following a function's declarator** (e.g. `template<class T> void f(T)
  requires std::integral<T>;`), whose prefix ends in the parameter list's
  closing `)` rather than a `template<...>` header's `>`. This is
  unambiguous — nothing but a trailing specifier can follow a function
  declarator's `)` before the terminating `;`/`{` in any C++ grammar,
  pre-C++20 included.
