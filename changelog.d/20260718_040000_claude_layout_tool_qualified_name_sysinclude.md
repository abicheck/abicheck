<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G28 Phase 4 layout enrichment**: two gaps silently lost the whole
  enrichment on otherwise-valid `--ast-frontend clang` dumps:
  - `dumper_clang.py` never populated `RecordType.qualified_name`, so the
    layout tool's per-record lookup (keyed by its own fully-qualified
    `getQualifiedNameAsString()` spelling) never matched any namespaced or
    nested type — only global-scope types got enriched. `qualified_name`
    is now populated the same way castxml's already is.
  - The layout tool's own compile command never threaded through the
    auto-probed host system include dirs (`dumper._clang_header_dump`'s
    libstdc++/libc parity probe), so a header set that only parsed because
    of that probe failed in the layout tool's separate invocation. Now
    re-probed and passed through identically.
