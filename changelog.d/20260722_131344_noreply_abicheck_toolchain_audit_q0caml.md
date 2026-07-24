### Fixed

- **The C++20 dialect detector now recognizes `std::ranges::`-constrained
  template parameters** (e.g. `template <std::ranges::range R> void
  f(R&&);`), a common real-world form the earlier `std::`-scoped
  constrained-parameter detection missed since `<ranges>` concepts live
  under a distinct namespace from `<concepts>`/`<iterator>`.
