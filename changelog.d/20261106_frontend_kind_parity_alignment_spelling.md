### Fixed

- **castxml and clang now agree on a builtin-typed global's alignment and on
  integer field retypes** — the clang header frontend records the
  compiler-probed natural alignment of a builtin-typed variable (also when it
  is `const`/`volatile`), as castxml already did, so `extern int v` becoming
  `extern long v` reports `var_alignment_changed` on both frontends, and a
  `const int` global no longer reports a spurious
  `exported_object_alignment_reduced` under clang. A struct field retyped
  from `int` to `long` no longer reports an extra `struct_field_type_changed`
  under clang only: DWARF's `long int` and clang's `long` are now read as the
  same type. Snapshots record extractor generation 43.
