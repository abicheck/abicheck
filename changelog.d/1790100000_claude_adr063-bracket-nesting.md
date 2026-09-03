### Fixed

- **The opaque-type by-value scan and `depth_aware_bare_name`'s scope split
  now also track square-bracket nesting.** Both `_is_indirect_spelling`
  (`abicheck/compare/opaque_types.py`) and `depth_aware_bare_name`
  (`abicheck/diff_helpers.py`) tracked parenthesis nesting to keep a
  relational comparison from being mistaken for a template delimiter, but
  an array-subscript comparison needs no surrounding parens to be valid
  C++ (`S<arr[1 > 0], dep::Tag>`) — its bracketed `1 > 0` still closed the
  outer template one `>` early, wrongly reading a genuinely-nested `&h` as
  top-level indirection, or wrongly splitting a leaf name inside a
  templated segment instead of at the real scope boundary. Square-bracket
  nesting is now tracked the same way parenthesis nesting is (Codex review
  on PR #1041).
