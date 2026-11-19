### Added

- **A field-level finding now names its member as a typed key.** `changes[]` and `suppression.suppressed_changes[]` entries carry an optional `field_name` (compare report schema 7.7): `f2` for a `type_field_offset_changed` on `s0::f2`. Before, `symbol` named only the record and the member was readable only from the `description` text. Additive; never affects a verdict, severity or exit code.
