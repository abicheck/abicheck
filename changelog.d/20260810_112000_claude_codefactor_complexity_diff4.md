### Changed

- **Split two more CodeFactor "Complex Method" findings in the DWARF struct-layout
  detector and the stdlib type-reachability spelling helper.**
  `diff_platform._diff_struct_layouts`'s four numbered sections each become their
  own function (`_struct_size_and_alignment_changes`, `_added_fields_by_offset`,
  `_removed_field_changes`, `_existing_field_changes`), so the reserved-field,
  rename and removal rules sit with the matching they constrain.
  `type_reachability.directly_referenced_stdlib_type_spellings` splits its four
  passes similarly, and its exact and trusted passes — structurally identical,
  differing only in which scan collections they start from — now share one
  `_alias_confirmed_identities`. Both behaviour-preserving and checked
  differentially against their pre-refactor selves: 40,002 runs over randomized
  field sets for the struct detector, and 12,000 runs over snapshots built to
  exercise every documented spelling-collision hazard for the reachability
  helper — no differences in either.
