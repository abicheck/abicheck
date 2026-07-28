<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **`dump` now excludes toolchain/system-header declarations by default.**
  A full header-AST dump previously serialized every declaration the parser
  saw, including the entire transitive `#include` dependency surface
  (SYCL/libstdc++ internals, etc.) regardless of whether the library under
  test even declares them itself — for a library with a large or
  heavily-templated dependency stack this could put the snapshot JSON in
  the hundreds-of-MB range. `dump` now drops a declaration whenever its own
  defining header is a toolchain/system header (`/usr/include`, MSVC
  `VC/Tools`, the Xcode/macOS SDK, ...) — a header-*origin* filter, not an
  ABI-visibility one: the library's own private/internal declarations are
  always kept, exactly like its public ones. Pass the new
  **`dump --include-dependencies`** to opt out and get the old, unfiltered
  full dump. This is a behavior change to `dump`'s default output — a
  baseline dumped before this change and one dumped after it may no longer
  compare identically if either side's public API touches a dependency
  type; re-dump baselines that need `--include-dependencies` parity.
