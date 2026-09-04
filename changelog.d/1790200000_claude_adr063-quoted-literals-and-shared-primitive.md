### Fixed

- **The opaque-type by-value scan and `depth_aware_bare_name`'s scope split
  now also skip quoted string/character literals**, and both were
  consolidated onto one shared, bracket-KIND-aware-stack primitive
  (`model.qualified_name_split.iter_top_level_chars`) instead of each
  carrying an independent flat depth counter. A quoted literal used as a
  non-type template argument (`S<'>', &h>` — valid C++, retained verbatim
  by clang) had the same problem the parenthesized/bracketed relational
  fixes closed one level up: the `>` inside the literal sat at neither
  paren nor bracket depth, so it still closed the outer template one `>`
  early, wrongly reading a genuinely-nested `&h` as top-level indirection
  (or misplacing a bare-name scope split). The new shared primitive mirrors
  `extract.semantic_normalizer_artifacts.has_unresolved_component`'s own
  hardened bracket-stack design — closing a further class neither prior
  fix set out to address directly: a real `>>` shift/comparison operator
  inside a parenthesized non-type template argument is not two template
  closers (Codex review on PR #1041).
