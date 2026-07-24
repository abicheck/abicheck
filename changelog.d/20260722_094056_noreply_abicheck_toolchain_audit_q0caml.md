### Fixed

- **The C++20 dialect detector now recognizes encoding-prefixed raw string
  literals** (`u8R"(...)"`, `uR"(...)"`, `UR"(...)"`, `LR"(...)"`), not just
  the bare `R"(...)"` form. The raw-string pattern's `\bR"` never matched
  after a prefix, since both the prefix's last character and `R` are word
  characters with no boundary between them — leaving a prefixed raw string
  completely unstripped, so text merely resembling a requires-expression or
  concept inside one could force `-std=gnu++20` unnecessarily.
