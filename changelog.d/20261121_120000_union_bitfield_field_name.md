### Fixed

- **`field_name` on union-member and bit-field findings** — `union_field_added`,
  `union_field_removed` and `field_bitfield_changed` now carry the report's
  structured `field_name` (schema 7.7's field-level member), like
  `union_field_type_changed` and the `type_field_*` kinds already did. Before,
  the member these findings named was readable only from `description`, so a
  consumer could not tell which member of a union was added or removed, or
  which bit-field changed width, without parsing prose. The JSON key is
  additive: older reports simply omit it.
