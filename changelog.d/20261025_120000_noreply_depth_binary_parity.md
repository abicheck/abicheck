### Fixed

- **A stored header snapshot compared at `--depth binary` is now judged on
  binary evidence** — `compare old.json new.json --depth binary` on two
  header-built snapshots now reports what a binary-only dump of the same
  libraries reports. Four fixes:
  - Struct and enum layout changes come through the type detectors
    (`type_size_changed`, `type_field_added_compatible`, `type_field_removed`)
    instead of the raw-layout fallback (`struct_size_changed`,
    `struct_field_removed`).
  - Header-only function attributes no longer produce findings at that depth.
    These were static, const/volatile and ref qualifiers, virtuality, access,
    deprecation and return/parameter spellings, which produced
    `func_static_changed`, `func_cv_changed`, `func_ref_qual_changed`,
    `func_deprecated_added` and `method_access_changed`.
  - An export no header declares is no longer lost. Losing a vtable,
    typeinfo or undeclared helper used to read `NO_CHANGE` at `--depth binary`
    and `BREAKING` without it; now it reads `BREAKING` at both.
  - Projecting a binary-only (debug-info) pair to `binary` is now the
    identity. A function that became hidden reads as
    `func_visibility_changed`, not `func_removed`.

  A type whose debug layout exists on only one side is no longer reported
  as removed or added. Residual differences are documented in the known
  gaps: function signatures, typedefs, bases and vtables that a header
  snapshot does not store.
