### Fixed

- **A dependent return type containing a quoted string or char literal
  whose contents happen to include an unbalanced paren character (e.g.
  `decltype("(")`) no longer corrupts `return_type`.** The clang header
  backend's return-type resolver's top-level paren scanner treated a
  literal's own `(`/`)` characters as real declarator structure, so a
  literal containing an unmatched `(` could make the scan's paren-depth
  counter never return to zero, silently swallowing the function's real
  trailing parameter-list group and reducing the reported return type to
  a truncated fragment. The scanner (and the sibling trailing-return-type
  arrow scan) now skips over quoted string/char literal spans entirely
  when tracking paren/bracket depth.
