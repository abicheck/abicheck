### Fixed

- **The C++20 dialect detector now recognizes abbreviated constrained
  function parameters** (e.g. `void f(std::integral auto x);` or `void
  f(std::ranges::range auto&& r);`), which are exactly equivalent to a
  `template<std::integral T> void f(T x);` declaration but have no
  `template<...>` header at all, so neither the earlier constrained-
  template-parameter pattern nor any other existing probe matched them.
