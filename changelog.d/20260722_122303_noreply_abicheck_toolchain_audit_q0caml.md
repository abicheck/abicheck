### Fixed

- **The C++20 dialect detector no longer assumes a call to a pre-C++20
  `requires` function is genuine C++20 syntax just because it's used as an
  operand.** `if (requires(1)) ...`, `!requires(1)`, and `x = requires(1);`
  are all valid pre-C++20 code (a call to an ordinary function literally
  named `requires`), and previously slipped past the detector because
  nothing but an operator/punctuation character — not a bare identifier —
  precedes the call. That case now falls back to the same
  requirements-body confirmation already used for the `return`/`throw`/
  `co_return` exception, so only a genuine requires-expression (with a
  confirmed `{ ... }` body) forces `-std=gnu++20`.
