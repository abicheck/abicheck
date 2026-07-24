### Fixed

- **The C++20 dialect detector's raw-string stripper now matches the full
  delimiter grammar** — any basic-source character except whitespace,
  parentheses, and backslash — instead of only identifier characters. A
  delimiter like `tag-` (containing a hyphen) is valid C++ and was
  previously left completely unstripped, so text merely resembling a
  requires-expression inside such a raw string could force `-std=gnu++20`
  unnecessarily.
