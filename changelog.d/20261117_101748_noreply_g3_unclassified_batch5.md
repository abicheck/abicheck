### Changed

- **`used_reserved_field` declares where it is observable (ADR-093).** The detector needs the field names (a header or DWARF): the header and DWARF pairs emit it, a stripped binary does not. Declared `{B,D}` and `{B,H}`. It leaves the UNCLASSIFIED baseline; with the two template and tag kinds in this batch the count goes 129 -> 126.
