### Fixed

- **A change to a public typedef no function uses is no longer dropped.**
  Header dumps with a public-header set now record each typedef's ADR-015
  origin from its declaring header (castxml, clang and the native gcc
  plugin), stored as the optional `abicheck.typedef_origins` extension fact
  on the declarations section (extractor generation 62). A typedef declared
  in a public header seeds the public surface, so `typedef_removed` and
  `typedef_version_sentinel` for an unused public typedef are reported; one
  from a private or system header stays out of surface. DWARF-only and
  older snapshots carry no typedef origin and read it as unknown, never
  private.
