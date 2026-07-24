### Fixed

- **The C++20 dialect detector now recognizes parameterless
  requires-expressions.** `requires { typename T::value_type; }` (no
  parenthesized parameter list, unlike `requires(T a) { ... }`) matched
  neither the requires-expression pattern (which required a `(`) nor the
  requires-clause pattern (which required a word character immediately
  after `requires`, not a bare `{`) — so a header using only this form was
  never detected as needing `-std=gnu++20`, and the CastXML invocation
  stayed on the host's default dialect and failed to parse it.
