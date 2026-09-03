### Fixed

- **The opaque-type by-value scan's indirection check is now template-depth
  aware.** `_is_indirect_spelling` looked for a `*`/`&` sigil anywhere in the
  rendered type text, so a by-value template specialization whose *template
  argument* happened to contain one of those sigils (`Callback<&ns::handler>`
  — a pointer/reference non-type template argument — or `Box<void (*)()>` —
  a function-pointer type argument) was wrongly treated as pointer/reference
  indirection at the outer declarator, letting a genuine by-value exposure
  escape detection and leaving the record wrongly `opaque` with its real
  layout change suppressed. Only a sigil at template depth zero — outside
  any `<...>` nesting — now counts, mirroring `diff_helpers.
  depth_aware_bare_name`'s own bracket-depth tracking (Codex review on
  PR #1041).
