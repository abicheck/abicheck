### Changed

- **`template_return_type_changed` and `tag_type_renamed` declare where they are observable (ADR-093).** Both need type information (the header, or DWARF) to fire: a stripped binary alone does not emit either, while the header pair and the DWARF pair do. Declared `{B,D}` and `{B,H}`. They leave the UNCLASSIFIED baseline (129 -> 127).
