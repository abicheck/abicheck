### Fixed

- **The C++20 constrained-template-parameter detector now tolerates
  nested concept arguments** (e.g. `template <std::same_as<std::vector
  <int>> T> void f(T);`), which the previous single-level `(?:<[^<>]*>)?`
  regex couldn't match since its excluded-character class stopped at the
  first inner `<`/`>`. Replaced with a manual bracket-depth scan (mirroring
  the existing parenthesized requires-expression handling) that tolerates
  arbitrary nesting.
