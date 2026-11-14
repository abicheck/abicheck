### Changed

- **`source_level_kind_changed` (struct ↔ class) is now COMPATIBLE** — a
  class-key change with the same members breaks neither source (a mismatched
  key is at most a warning) nor the Itanium ABI, so it no longer gates as an
  API break (ADR-011 amendment, 2026-11-14). Its consequences keep their own
  grades: default-access changes as `field_access_changed` /
  `method_access_changed` / `base_class_access_narrowed`, and MSVC's class-key
  mangling as a PE export removal (BREAKING). The now-redundant `sdk_vendor`
  override for the kind is removed; a run that exited 2 on a bare struct ↔
  class change now exits 0.
