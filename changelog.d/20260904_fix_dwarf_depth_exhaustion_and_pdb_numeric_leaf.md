### Fixed

- **DWARF basic/advanced channels now mark evidence incomplete on depth-
  exhausted type resolution** — `dwarf_metadata.py`'s `_die_to_type_info`
  (`depth > 8` guard) and `dwarf_advanced.py`'s `_unwrap_qualifiers`
  (12-iteration cap) are each the only thing that stops a cyclic type
  chain from recursing forever (such a chain can never be caught by their
  memoization caches alone, since a cache is only written *after* a call
  returns). Both previously substituted a placeholder without touching
  the completeness accumulator, unlike every other placeholder-
  substitution site in the same call chains — a cyclic or genuinely deep
  typedef/qualifier chain could leave the basic or advanced channel
  reporting `parsed` despite a field, return, or parameter type — or a
  value-ABI trait — silently degraded.
- **PDB TPI parsing now marks a record incomplete when it contains an
  unsupported CodeView numeric-leaf tag** — `_read_numeric_leaf()`
  silently substituted 0 for the leaf's real value on an unrecognized
  tag, indistinguishable from a legitimately-zero-valued leaf to every
  caller that only reads back the returned value. A discarded struct/
  array `byte_size`, member/enumerator offset-or-value, or base-class
  offset could leave the enclosing record's own completion verdict (and
  `TypeDatabase.failed_record_count`) reading success even though a size,
  offset, or enum value was silently discarded.
