<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G28 Phase 4 layout tool**: `abicheck-clang-layout-tool` keyed
  `RecordType.base_offsets` by Clang's fully-qualified
  `getQualifiedNameAsString()` (e.g. `"ns::Base"`), but castxml and the
  DWARF backend both key that same field by the bare, unqualified base
  name — `_check_base_offsets` does an exact key lookup, so a namespaced
  base's real offset change was silently missed when comparing a
  castxml/DWARF baseline against a new `--ast-frontend clang` dump enriched
  by the layout tool. Now normalized to the bare name before storing,
  matching every other backend's convention.
- The layout tool's own second clang pass had no equivalent of
  `dumper._clang_header_dump`'s two recovery retries — the C→C++ self-heal
  (a pure-`#include` C++ umbrella header initially guessed as C) and the
  graceful `#error`-header exclusion (a header not meant for direct
  inclusion). Without them, a header set that only parsed via one of those
  retries on the main dump would silently lose the *entire* layout
  enrichment on this second, independent pass. Both retries are now
  mirrored here, reusing the same shared `dumper_clang_errors` driver
  (adapted for the tool's `"ok"`-in-JSON success signal instead of a
  process exit code, since the tool always exits 0).
