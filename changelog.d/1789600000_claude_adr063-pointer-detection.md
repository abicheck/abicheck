### Fixed

- **The opaque-type by-value scan now recognizes a cv-qualified pointer
  declarator regardless of spacing.** `find_by_value_types`'s pointer check
  (`text.endswith("*")` or `"* " in text`) missed `"Handle *const"` and the
  no-space `"Handle*const"` form clang/castxml can also emit, wrongly
  treating a genuinely pointer-only reference as a by-value exposure —
  dropping a genuinely opaque type out of both identity tiers and reporting
  its private layout change as breaking. Any `*` in the rendered type text
  is now sufficient evidence of at least one pointer indirection level
  (Codex review on PR #1041).
