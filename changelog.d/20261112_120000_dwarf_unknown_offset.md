### Fixed

- DWARF member offsets can now be unknown. `FieldInfo.byte_offset` is
  `int | None` (`None` = unknown). A snapshot member that lacks a
  `byte_offset` key decodes as unknown instead of offset 0, so a missing
  location can no longer fabricate a `struct_field_offset_changed` finding.
  A stored `0` still decodes as `0`. abicheck writes the key for every member,
  so this changes only hand-written or foreign snapshots; no `SCHEMA_VERSION`
  bump is needed because the written shape is unchanged. The `byte_size == 0`
  "unknown" convention is audited and its residual gaps are recorded in
  `docs/contribute/known-gaps.md`.
