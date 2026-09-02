### Fixed

- **DWARF type-name reconstruction now spells a const pointer
  (`int * const`) distinctly from a pointer to const data (`const int
  *`)** — both used to render as the identical `"const int *"` text
  since a cv-qualifier wrapping a pointer/reference DIE was always
  printed as a prefix. `extract.dwarf_records.format_qualified_type_name`
  now places the qualifier on the correct side of the declarator.
