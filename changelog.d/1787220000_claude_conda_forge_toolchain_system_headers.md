### Fixed

- **A conda-forge/pixi (or any other relocatable, non-`/usr`) GCC/Clang
  toolchain's own private headers were not recognized as system headers.**
  `_SYSTEM_HEADER_DIRS` only matched a handful of fixed, OS-rooted prefixes
  (`/usr/include`, an Xcode/MSVC SDK root, ...); a relocatable toolchain puts
  its private headers under an arbitrary environment prefix instead (e.g.
  `<env>/lib/gcc/x86_64-conda-linux-gnu/14.3.0/include/c++/...`), which
  matched none of them and so was diffed as ordinary project surface —
  reported as a false `func_removed`/`func_added` breaking finding for
  libstdc++'s internal `_Iter_pred` predicate helper from abicheck's *own*
  GitHub Action toolchain. `provenance.py` now recognizes GCC's private
  include tree (`lib/gcc/<triple>/<version>/include[-fixed]`), libstdc++'s
  per-version public tree (`include/c++/<version>`), and Clang's private
  builtin headers (`lib/clang/<version>/include`) *structurally* — with the
  toolchain's own version/target-triple path component wildcarded — rather
  than by a fixed literal prefix, so this holds regardless of where the
  toolchain is installed.
- **A `..` path segment (e.g. a build-recorded compiler path resolved via
  `$(dirname "$CC")/..`, producing a literal `bin/..` component) was not
  lexically collapsed before path-segment matching**, so a real path like
  `.../bin/../lib/gcc/.../include` didn't match its already-collapsed
  `.../lib/gcc/.../include` form. `provenance._segments()` now collapses a
  `..` against its preceding segment (purely textual — no filesystem access,
  keeping this module's existing "match by segments, never by resolving real
  paths" design), fixing every path-segment match in the module, not just
  the toolchain-header case above.
