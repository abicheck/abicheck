### Fixed

- **The occurrence-relative opaque-type by-value scan now checks every
  matching occurrence of a type name, tracks brace nesting, and honors an
  enclosing pointer.** Three gaps in the occurrence-relative redesign
  (Codex review on PR #1041, follow-up round):
  - A type name repeated in the same declarator (e.g.
    `"Pair<Handle*, Handle>"`) only had its *first* occurrence checked, so
    a genuinely by-value second occurrence could be missed if the first
    happened to be indirect. `_type_is_by_value_referenced` now iterates
    every occurrence (`re.finditer`, via renamed `_type_token_matches`/
    `_unqualified_type_token_matches`) and reports by-value as soon as any
    single one is.
  - A C++20 structural non-type template argument's own braced
    initializer (`"S<A{1 < 2}>"`, which clang can render verbatim) had its
    internal `<` mistaken for a template opener, since the bracket-depth
    stacks in `model.qualified_name_split.iter_top_level_chars` and
    `skip_template_arguments` didn't track `{`/`}` the way they already
    tracked `(`/`[`. Both now push/pop `{`/`}` identically to the other
    bracket kinds.
  - A pointer *enclosing* a matched occurrence's own template arguments
    (`"Pair<Handle>*"`) wasn't recognized as making that occurrence
    indirect, since `_occurrence_is_indirect` only checked the
    occurrence's own immediate declarator position. It now also walks
    every bracket level enclosing the occurrence outward (new
    `model.qualified_name_split.enclosing_close_positions` primitive) and
    treats a `*`/`&` at any enclosing level's own close position as
    indirect too.

  `diff_helpers.depth_aware_bare_name` shares the same
  `iter_top_level_chars` primitive, so it gains the same braced-
  initializer fix.
