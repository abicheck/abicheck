### Added

- **`RecordType`, `EnumType`, `Function` and `Variable` now carry a resolved
  `entity_id`** (ADR-063 Phase 2), computed once by whichever header-AST
  backend parsed the declaration, from the typed scope path that backend
  records at the moment each scope is entered. This closes a class of
  identity collision a flattened `qualified_name` cannot express -- most
  directly a record nested in a record versus the same bare names nested in
  a namespace, which spell an identical `"B::C"` -- and does so for
  `castxml` and direct-`clang` alike, verified against both producers'
  real output. The field is keyword-only, defaults to `None` for a caller
  constructing one of these public dataclasses directly (never a fabricated
  identity), and is excluded from equality, so no existing behavior,
  comparison result or snapshot document changes. It is **not persisted**
  in this release: a snapshot written to disk and reloaded carries
  `entity_id=None`, because encoding it faithfully requires a wire schema
  that preserves the typed scope segments rather than flattening them back
  into a string. No diff or report consumer reads the field yet.
