### Fixed

- **The opaque-type by-value scan now recognizes a declarator whose
  pointer/reference sigil is wrapped in a grouping paren after the type
  name.** `"Handle (*)[3]"` (pointer to an array of `Handle`) and
  `"Handle (*)(int)"` (pointer to a function returning `Handle`) -- the
  standard C/C++ spelling whenever a plain trailing `*` would otherwise
  bind to the wrong part of the declarator (an array/function suffix
  binds tighter than a bare pointer) -- were both wrongly classified as
  by-value, since neither the occurrence's own immediate-suffix check nor
  the enclosing-bracket walk sees the `*` inside a paren that opens
  *after* the matched occurrence's end. `_sigil_follows` now also
  recognizes this declarator-grouping-paren shape (optionally prefixed by
  a pointer-to-member `Class::` qualifier), closing the class of false
  by-value classifications for a pointer whose sigil is parenthesized
  rather than bare (Codex review on PR #1041, follow-up round).
