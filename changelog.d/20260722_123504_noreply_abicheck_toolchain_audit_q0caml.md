### Fixed

- **The C++20 dialect detector now recognizes a parenthesized
  requires-clause on the same line as its `template<...>` header** (e.g.
  `template<class T> requires (sizeof(T) > 4) void f(T);`). A clause has
  no trailing `{` body, so it was previously misjudged as a plain
  pre-C++20 call by the operand-context body check, and the separate
  requires-clause pattern didn't match either since the next token is `(`
  rather than a word. Mirrors the same-line check already used for
  `concept` declarations.
- **Hidden-friend surface classification now also resolves an owner
  stored as a legacy full qualified `RecordType.name`** (e.g. `"ns::Foo"`
  set directly on `name` rather than via a separate `qualified_name`
  field). Such a side previously fell through to the bare-tail fallback,
  which never matches because `all_types` only ever indexes a record's
  own `name`, never a tail extracted from it — so a confidently-public
  legacy-style side was wrongly treated as absent while the other,
  qualified-style side's private/system origin silently demoted the
  finding.
