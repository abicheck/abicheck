<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- The whole-snapshot dump cache didn't account for the G28 Phase 4 layout
  tool's availability/identity: since `service.run_dump` enriches every
  `"clang"`-backend dump with `attach_clang_layout`, a cache entry created
  before enabling/changing `ABICHECK_CLANG_LAYOUT_TOOL` could be silently
  reused afterward (or vice versa) instead of re-running the real dump.
  The cache key now includes the resolved layout-tool identity for
  `"clang"`-backend dumps (irrelevant, and so omitted, for castxml/hybrid).
- The layout tool's compile-flags slicing searched for the first bare
  `"-Xclang"` in the command, but a user's own `-Xclang <arg>` passed
  through `--gcc-options`/`--gcc-option` sits before abicheck's own
  appended `-Xclang -ast-dump=json` tail — stopping at the user's flag
  dropped it plus every later shared flag (system includes, language
  mode). Now searches for the specific adjacent `-Xclang`/`-ast-dump=json`
  pair instead.
