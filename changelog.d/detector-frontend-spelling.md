### Fixed

- **The internal-type leak check no longer depends on how a header frontend
  spells a type** — `internal_type_leaks_via_public_api` resolves every base,
  field and signature type to the record it names by C++ name lookup from the
  scope that wrote it (template arguments included), using castxml's recorded
  slot identities when present, instead of matching the written spelling.
  Under `ABICHECK_AST_FRONTEND=clang` a changed internal type returned or taken
  by value (`detail::Impl make()`) or nested in a template argument
  (`detail::Box<detail::Box<int>>`) now reports its leak. Under both frontends
  an internal record that shares its leaf with a public one (`lib::detail::Impl`
  next to `lib::Impl`) is no longer dropped from the walk, and a layout change
  to an internal type reached only through a pointer is demoted as unreachable
  churn instead of gating the verdict as `BREAKING`.
