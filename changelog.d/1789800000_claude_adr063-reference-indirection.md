### Fixed

- **The opaque-type by-value scan now recognizes a reference declarator as
  indirection.** `find_by_value_types`'s indirection check recognized only
  `*` (pointer sigils), so `Handle&`/`Handle&&` was wrongly counted as a
  by-value exposure — a reference never exposes the referent's own layout
  by value. A genuinely opaque type referenced only by reference was
  dropped out of both identity tiers and its private layout change
  reported as breaking. Any `&` anywhere in the rendered type text is now
  also sufficient evidence of indirection (renamed `_is_pointer_spelling`
  to `_is_indirect_spelling` to match) (Codex review on PR #1041).
