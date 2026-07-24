### Fixed

- **The C++20 dialect detector's `return`/`throw`/`co_return` exception now
  requires a confirmed requirements body.** `return requires(1);` (a plain
  call to a pre-C++20 function named "requires") is just as syntactically
  valid after those keywords as a genuine `return requires(T t) { t.foo();
  };` — only the latter carries a requirements body, so the exception now
  checks for a `{` immediately after the parenthesized parameter list's
  closing `)` before accepting.
