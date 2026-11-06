### Fixed

- **A dump that discarded its headers no longer reads `complete` assurance** —
  when `--header` was given but the header-scoped dump fell back to the export
  table (`scope_fallback`: no header declaration matched an export, e.g. a
  header-only template surface on Mach-O/PE, or the header backend failed),
  the header-level dimensions were never assessed yet the comparison could
  still report `complete`. The rollup now names "header declarations (header
  scoping fell back to the export table)" for each such side and reads
  `partial`. Empty Mach-O/PE header-parse toolchain metadata also now defaults
  to castxml, so the concept and template-default header scans run there.
