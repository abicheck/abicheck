### Fixed

- **native GCC header backend: record, enum and field deprecation** — the GCC
  plugin now emits the deprecation marker (message, or empty for a bare
  attribute) of a struct/class/union, an enum and a data member, and the
  projection stores it, so `type_deprecated_*`, `enum_deprecated_*` and
  `field_deprecated_*` are reported with the gcc header backend as with
  castxml. The plugin protocol schema is now 3: rebuild the plugin
  (`python -m abicheck.extract.headers.gcc.build`); an older plugin is
  refused instead of reading every type as not deprecated.
  `EXTRACTOR_GENERATION` 56, snapshot cache salt v50.
