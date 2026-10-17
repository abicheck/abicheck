### Fixed

- **An appended field is no longer "compatible" when vtables could not be
  seen** — a struct whose producer cannot capture vtables or virtual bases
  (PDB layout's `unsupported`, a `failed` extraction, or known-incomplete
  DWARF coverage with no entries) is no longer read as non-polymorphic. A new
  field on such a struct is now `type_field_added` (breaking) instead of
  `type_field_added_compatible`, so the opaque-handle rule no longer drops its
  size change. A confirmed standard-layout or trivially-copyable trait still
  proves the struct non-polymorphic. An omitted (`not_collected`) vtable keeps
  its existing meaning of "no virtuals" for typed-API and flat-C snapshots.
  The decision moved from `diff_types.py` to `compare/field_addition.py`.
