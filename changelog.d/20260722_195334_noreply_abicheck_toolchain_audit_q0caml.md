<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **C++20 auto-detection now recognizes the `<iterator>` `indirectly_*`
  concepts and `sized_sentinel_for`.** `_CPP20_STD_CONCEPT_NAMES`
  (`abicheck/dumper_ast_config.py`) omitted `indirectly_readable`,
  `indirectly_writable`, `indirectly_swappable`,
  `indirectly_movable`/`indirectly_movable_storable`,
  `indirectly_copyable`/`indirectly_copyable_storable`,
  `indirectly_comparable`, `indirectly_unary_invocable`, and
  `sized_sentinel_for`. A header whose only C++20 signal was one of these
  in a constrained template parameter (e.g. `template
  <std::indirectly_readable I> void f(I);`) was still parsed in C++ mode
  via the bare `template` keyword, but without `-std=gnu++20` — on a
  C++17-default toolchain the concept itself is unavailable there, so the
  L2 header scan failed.
</content>
