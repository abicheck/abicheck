### Fixed

- **The C++20 concept-declaration detector no longer treats a pre-C++20
  variable template of a type literally named `concept` as genuine C++20
  syntax.** `template<class T> concept C = {};` has the identical textual
  shape as a real concept definition even with a preceding `template<...>`
  header, but a concept's constraint-expression can never be a bare
  brace-init-list (`{}`/`{...}` isn't a valid constant-expression there),
  so that shape is now excluded.
