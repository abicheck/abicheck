### Fixed

- **`--show-only`'s dangling-correlation cleanup now matches by qualified
  identity only when one is available, never falling back to the bare
  symbol too** — a finding correlated via `qualified_name` (the
  `layout_unverifiable`/`type_vtable_changed` pairing) previously also
  checked `symbol` as an alternative match, which let an unrelated
  same-leaf-name record's own surviving finding in a different namespace
  be mistaken for the filtered-out target, wrongly keeping the "See also"
  reference. Symbol-based matching is now used only for correlations that
  have no qualified identity to begin with (the older
  `public_api_internal_dependency_added` pairing).

