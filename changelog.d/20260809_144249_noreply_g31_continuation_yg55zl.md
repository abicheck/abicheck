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
- **Fixed two follow-on gaps in the same-named work above**: a declaration
  present on both snapshot sides only via the clang leg of a
  `--ast-frontend hybrid` merge now gets its `deprecated`/`is_scoped`
  provenance recorded (previously it silently fell back to "unknown
  producer" and the comparison was skipped even though the value was
  genuinely known); and `RecordType`/`EnumType` matching in
  `dumper_hybrid.merge_snapshots` and the class-layout descriptor detector
  (`STANDARD_LAYOUT_LOST`/`TRIVIALLY_COPYABLE_LOST`/`BASE_CLASS_OFFSET_CHANGED`/
  `VPTR_INTRODUCED`/`TAIL_PADDING_REUSE_CHANGED`) now uses namespace-qualified
  identity instead of the bare declaration name, so two distinct types
  sharing only a bare leaf name in different namespaces (e.g. `a::Foo` /
  `b::Foo`) no longer collide.

