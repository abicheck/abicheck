### Fixed

- **`diff_types` no longer diffs typedefs through the bare-name-collapsed
  `AbiSnapshot.typedefs` dict when the qualified twin is available.**
  `AbiSnapshot.typedefs` is keyed by unqualified alias name on both header
  backends, so two unrelated member typedefs sharing a bare spelling in
  different classes (e.g. `X::impl_value_t` declared on many unrelated
  classes — an ordinary STL-container-shaped pattern) collapse onto one dict
  entry, with whichever declaration the backend visited last winning. Diffing
  that collapsed dict directly could flip the surviving entry's recorded
  value whenever an *unrelated* class gained or lost its own same-named
  alias, fabricating a spurious `typedef_base_changed` for a typedef that
  never itself changed. `_diff_typedefs` now prefers
  `AbiSnapshot.typedefs_qualified` (schema v25, unique per declaration)
  whenever both sides populate it, falling back to the legacy bare-keyed
  diff for a DWARF-only or pre-v25 snapshot.
- **The direct-clang header-AST backend now strips an embedded absolute
  source path out of `RecordType`/`EnumType` `name`/`qualified_name` the
  same way `dumper_castxml.py` already does.** A lambda closure/anonymous
  type's own spelling (or an enclosing scope segment) can carry
  `"(lambda at <path>:<line>:<col>)"`-shaped text; left unstripped on the
  clang backend, two checkout directories of the identical, unchanged
  declaration produced two different type identities and could manufacture
  a spurious `type_removed`/`type_added` pair.
