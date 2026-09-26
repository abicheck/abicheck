### Fixed

- **A header the clang parse dropped no longer silently removes its types
  from the comparison.** When `-H` names a whole directory, clang's `#error`
  retry drops top-level headers that refuse direct inclusion and parses the
  rest; that was only logged. A public type declared in such a header was then
  named by no header entity, so the dependency scope stripped its DWARF layout
  from the snapshot and the L1 debug-type scope never diffed it. The dropped
  headers are now recorded (`AbiSnapshot.ast_toolchain["header_parse_excluded"]`,
  kept across AST-cache hits by a sidecar beside the cache entry), the
  header-AST coverage record reads `partial` covering nothing
  (`reason: header_parse_excluded`), the dependency scope keeps every DWARF
  type except confirmed dependency types, and the report's "Relationship
  coverage" section counts each debug type no parsed header names as
  `unknown` instead of `proven_absent`. The `#error` retry moved to
  `abicheck/extract/headers/clang/error_header_retry.py`. No schema change.
