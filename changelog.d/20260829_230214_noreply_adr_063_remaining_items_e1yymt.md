### Fixed

- **A trailing GNU `__attribute__((...))` clause no longer corrupts or
  leaks into a function's reported return type.** The clang header
  backend's return-type resolver's scan-from-end fallback mistook a
  trailing attribute's own argument-clause group for the function's real
  parameter list, swallowing both the real parameter list and the
  attribute text into what was reported as the return type; a
  function-pointer-returning ("spiral") declarator's own trailing
  attribute leaked verbatim into its return type instead of being
  dropped. Unlike an exception specification (part of the function's
  type since C++17), a GNU attribute is never part of the type, so it is
  now excluded when locating the real parameter list and stripped
  entirely from the reported return type in both cases.
