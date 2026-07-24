### Fixed

- **The C++20 dialect detector now recognizes a trailing requires-clause
  after any number of cv/ref-qualifiers and specifiers** (`const`,
  `noexcept`, `override`, `final`, `&`, `&&` — e.g. `void f(T) const
  noexcept requires std::integral<T>;`), and a **parenthesized** trailing
  clause (`void f(T) requires (sizeof(T) > 4);`), which previously fell
  through the requires-expression body-check even though a clause has no
  body to confirm.
- **The opt-in `--allow-ast-frontend-fallback`/`ABICHECK_ALLOW_AST_FALLBACK`
  clang fallback now also triggers on an `UnsupportedCastxmlVersionError`**
  (the proactive CastXML version-gate check), not just the two narrower
  stderr-text signatures it previously recognized. A user who has
  explicitly opted into accepting the castxml/clang discrepancy risk had
  that opt-in silently defeated for the one new reason a castxml build can
  now be rejected.
