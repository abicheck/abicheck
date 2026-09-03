### Fixed

- **The opaque-type by-value scan now classifies indirection relative to
  the matched type occurrence, not the whole rendered text.** The
  whole-text top-level sigil scan (`_is_indirect_spelling`) missed a
  genuine pointer declarator when the matched type name's own `*`/`&`
  sits nested inside an unrelated part of the declarator — e.g. an
  implementation record `ns::Handle` referenced only through a public
  function-pointer parameter/return like `"void (*)(Handle*)"`: the `*`
  genuinely makes `Handle` pointer-only, but it lives inside the
  function-pointer's own nested parameter-list parens, which the
  whole-text top-level scan correctly ignored as belonging to a
  *different* part of the declarator — wrongly treating `ns::Handle` as
  exposed by value and reporting its private layout change as breaking.
  Indirection is now decided by what immediately follows the *specific*
  matched occurrence (skipping the occurrence's own template arguments as
  one unit via the new `model.qualified_name_split.skip_template_arguments`,
  then whitespace/cv-qualifiers), which answers the question directly
  instead of approximating it with bracket-depth tracking over the whole
  text — closing this gap without reintroducing any of the parenthesized-
  relational/array-subscript/quoted-literal false positives the prior
  whole-text design needed dedicated fixes for (Codex review on PR #1041).
