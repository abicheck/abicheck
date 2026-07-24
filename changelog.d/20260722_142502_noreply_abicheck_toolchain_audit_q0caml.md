### Fixed

- **The C++20 dialect detector now recognizes a trailing requires-clause
  after a trailing return type** (e.g. `auto f(T) -> int requires
  std::integral<T>;`, or the parenthesized form `auto f(T) -> int
  requires (sizeof(T) > 4);`), whose prefix ends in the return-type token
  rather than the declarator's `)`/`>` directly. The return-type
  expression itself can't be bounded generically, but a `->` anywhere
  before `requires` is on its own a sufficient, unambiguous signal.
