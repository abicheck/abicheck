### Fixed

- `overload_ambiguity_introduced` no longer reports a declaration retyped under
  the same symbol (`f(int)` -> `f(long)`) as an added overload.
