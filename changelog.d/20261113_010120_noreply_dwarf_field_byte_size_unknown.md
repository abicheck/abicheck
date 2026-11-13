### Fixed

- DWARF member type sizes can now be unknown. `FieldInfo.byte_size` is
  `int | None` (`None` = unknown), the same change `byte_offset` got. The
  DWARF producer records `None` when a member's type size is not evaluable
  and a real `0` for a flexible array member, so a `0 -> N` size change is now
  reported as `struct_field_type_changed` and an unknown size never is. A
  renamed field pairs even when one side's size is unknown. A snapshot member
  that lacks a `byte_size` key decodes as unknown; a stored `0` still decodes
  as `0`, so a legacy snapshot's unknown `0` is kept as `0`. The
  `byte_size == 0` convention is audited and closed in
  `docs/contribute/archive/known-gaps-closed.md`.
