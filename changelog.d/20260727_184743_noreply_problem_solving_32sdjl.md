<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **`dump --public-surface-only`** — scope a written snapshot to its public
  ABI surface (public functions/variables plus the types transitively
  reachable from their signatures, fields, and bases) instead of serializing
  every declaration the header AST parser saw. A full header-AST dump
  includes the entire transitive `#include` dependency surface regardless of
  whether the library's own public API reaches it, which can dominate the
  snapshot size for a library with a large or heavily-templated dependency
  stack (e.g. SYCL/libstdc++). The new flag reuses the same public-surface
  reachability closure `compare` already uses to demote out-of-scope
  findings (`surface.compute_public_surface`), so a type actually named in a
  public signature (including a used `std::`/SYCL type) is still kept — only
  unreferenced dependency internals are dropped. Requires a header-AST-derived
  snapshot (`-H`/`--header`, or `--dump-manifest`); a DWARF-only,
  export-table-only, or source-only dump has nothing to scope from and is a
  usage error rather than a silently unscoped or empty artifact. Filters the
  flat function/variable/type/enum/typedef lists only — an embedded
  header-only semantic graph (attached by default) is not yet filtered by
  this flag.
