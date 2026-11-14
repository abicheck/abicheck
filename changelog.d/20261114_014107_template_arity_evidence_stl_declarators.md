### Fixed

- `mandatory_template_param_added` no longer compares a class-template
  pattern's mandatory-parameter count (from recorded defaults) with an
  instantiation's spelled argument count: each evidence kind is compared only
  with itself, and a template whose two sides share no evidence kind reports
  nothing. A clang-side `template<class T, class U = int>` against a
  castxml-side `Foo<int, int>` was a false break.
- `mandatory_template_param_added` keys class templates by their qualified
  name, so `a::Box` gaining a mandatory parameter is reported even when an
  unchanged `b::Box` shares its bare name.
- `public_api_exposes_stl_by_value` keeps a slot's cv-qualifiers and
  declarators when substituting a bare alias (`string`) with its resolved
  `std::` identity, so a `const string&`, `string&&` or `string*` return is no
  longer reported as a `std::` type by value.

### Changed

- `count_top_level_template_args` moved from `abicheck.diff_templates` to
  `abicheck.compare.template_pattern_arity`.
