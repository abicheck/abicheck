### Fixed

- **The C++20 dialect detector no longer misdetects "concept" used as an
  ordinary pre-C++20 qualified type name.** `concept` only became a
  reserved keyword in C++20 — a qualified reference such as `ns::concept C
  = {};` is valid in any earlier standard, but was previously matched as a
  concept declaration, forcing `-std=gnu++20` and breaking a header that
  would otherwise have parsed correctly. A concept-name is always declared
  bare, directly after its own `template<...>` header, never out-of-line
  via a scope-resolution qualifier, so `::concept` can now only mean the
  pre-C++20 identifier usage.
