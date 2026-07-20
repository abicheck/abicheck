### Changed

- **AST caches and snapshots now identify the exact producer toolchain** —
  CastXML, Clang, host compiler, and layout-tool identities include executable
  content SHA256 values in cache keys and persisted provenance. Automatic
  CastXML-to-Clang fallback is now fail-closed unless explicitly enabled with
  `--allow-ast-frontend-fallback` or `ABICHECK_ALLOW_AST_FALLBACK=1`, and an
  enabled fallback records its reason in schema-v11 snapshots.
