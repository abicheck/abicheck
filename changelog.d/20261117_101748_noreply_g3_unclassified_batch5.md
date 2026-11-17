### Changed

- **Five more kinds declare where they are observable (ADR-093).** `template_return_type_changed`, `tag_type_renamed`, and `used_reserved_field` need type information (a header or DWARF) and are declared `{B,D}`, `{B,H}`; `opaque_invariant_broken` and `handle_type_changed` are header-level facts and are declared `{B,H}`. A stripped binary alone emits none of them, and each pair (header or DWARF) that the oracle covers emits its kind. They leave the UNCLASSIFIED baseline (129 -> 124).
