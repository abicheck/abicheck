<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **The direct-clang (`--ast-frontend clang`) L2 header backend now
  populates `deprecated` (on functions, variables, fields, types, and
  enums), `EnumType.is_scoped`, and `RecordType.is_standard_layout`/
  `is_trivially_copyable`** — previously castxml-only (or, for the last
  two, populated by neither backend), which left the `FUNC_DEPRECATED_*`/
  `VAR_DEPRECATED_*`/`FIELD_DEPRECATED_*`/`TYPE_DEPRECATED_*`/
  `ENUM_DEPRECATED_*`, `ENUM_BECAME_SCOPED`/`ENUM_LOST_SCOPED`, and
  `STANDARD_LAYOUT_LOST`/`TRIVIALLY_COPYABLE_LOST` detectors silently dead
  on a `--ast-frontend clang` run. A castxml-vs-clang or clang-vs-clang
  comparison for `deprecated`/`is_scoped` now correctly detects a real
  transition instead of being unconditionally skipped as a producer
  mismatch.

