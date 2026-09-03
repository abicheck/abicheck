### Fixed

- **DWARF variable `is_const` now walks the whole leading cv-qualifier
  run, not just the immediate wrapper DIE** — GCC nests
  `DW_TAG_volatile_type` (outer) around `DW_TAG_const_type` (inner) for
  a `const volatile` variable, so checking only the immediate type DIE
  silently reported `is_const=False` for a genuinely const-qualified
  variable. `extract.dwarf_records.variable_is_const` fixes this for
  both qualifier orders.
