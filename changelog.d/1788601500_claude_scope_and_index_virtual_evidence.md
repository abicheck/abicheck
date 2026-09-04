### Fixed

- `diff_vtable_layout._is_polymorphic`'s retained-virtual-`Function`
  positive-evidence path (added this session) is now gated on
  `not vtable_facts_reliable`, scoping it to its actual motivating case
  (a legacy pre-v21 direct-clang snapshot, whose `Function` entries are
  header-AST-sourced) rather than firing for any snapshot. A DWARF-sourced
  `Function.is_virtual` can itself carry the same per-translation-unit
  capture gap `diff_types_vtable.py`'s own module docstring documents and
  accepts for its "class's own virtual functions" branch — a spurious
  match there only ever widens "keep this finding," but here it directly
  settles a record's own polymorphism and could fabricate
  `SECONDARY_VTABLE_GROUP_CHANGED`.

### Performance

- The same evidence path is now backed by
  `diff_types_vtable._virtual_signatures_by_owner`, a one-time per-snapshot
  owner index, instead of `_owned_virtual_signatures_for_record` rescanning
  the whole function map on every query — turning a potential
  O(reachable types × functions) walk back into O(functions) + O(1)
  lookups. The index is now only built at all when a side is actually
  unreliable, so the overwhelmingly common reliable-producer case pays
  nothing extra.
