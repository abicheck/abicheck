### Fixed

- **`compare`/`scan` could crash with `TypeError: unhashable type: 'list'`
  on a comparison containing a finding whose `Change.old_value`/`new_value`
  is a list** (e.g. `PYTHON_STABLE_ABI_VIOLATION`, whose `new_value` is a
  `list[str]` of offending imports, despite `Change`'s own declared
  `old_value`/`new_value: str | None` type). `diff_filtering._dedup_cross_kind`
  (the struct/type cross-tier dedup) indexed *every* change's
  `cross_tier_transition()` into a `set`, regardless of kind, even though
  only the AST-tier kinds named in `_DWARF_TO_AST_EQUIV`'s values are ever
  looked up — a list is unhashable, so building the index crashed for any
  comparison producing an unrelated finding of this shape. Now only
  indexes the kinds this dedup actually queries.
