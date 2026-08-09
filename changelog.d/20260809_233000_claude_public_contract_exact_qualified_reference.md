<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`--contract-evaluation --contract public` no longer over-rejects an exact,
  fully-qualified public reference.** When a public signature names a type
  fully qualified (e.g. `ns1::Point`) and an unrelated sibling elsewhere in
  the snapshot shares the same bare tail (`ns2::Point`), the evaluator
  previously reported `UNKNOWN_UNRESOLVED` for both, indistinguishable from a
  genuinely ambiguous bare-only reference. `PublicSurface` now records which
  qualified identities were reached via an exact, unambiguous spelling
  (`exact_type_identities`), so `ns1::Point` confirms `IN_CONTRACT` while a
  type reached *only* through a shared, ambiguous bare tail (no side ever
  spells the qualifier) is still correctly left unresolved. The closure's
  own type index (`_index_surface_types`) now also keys on `RecordType`/
  `EnumType.qualified_name`, not just the bare `.name` and its trailing
  `::` segment, so this fix applies under the bare-name-plus-separate-
  qualified-name convention (castxml/clang) as well as DWARF's
  qualifier-baked-into-`.name` convention.
