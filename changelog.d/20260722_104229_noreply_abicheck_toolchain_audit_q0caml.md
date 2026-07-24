### Fixed

- **The C++20 dialect detector no longer misdetects "requires" used as an
  ordinary pre-C++20 identifier.** `requires` only became a reserved
  keyword in C++20 — a declaration like `bool requires(int x) { ... }` is
  valid in any earlier standard, but was previously matched as a
  requires-expression, forcing `-std=gnu++20` and breaking a header that
  would otherwise have parsed correctly (the identifier is no longer usable
  under C++20). A bare identifier directly preceding `requires(` — with
  only whitespace, no operator — can now only mean a declaration/call using
  "requires" as a plain name, except for a small set of keywords
  (`return`/`throw`/`co_return`) that can legitimately introduce a genuine
  requires-expression the same way.
