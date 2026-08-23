### Fixed

- **Public-surface scoping (`--scope-public-headers`, the default) classified
  every header transitively `#include`d by a `-H`/`--header` umbrella header
  as `private-header`**, unless it happened to also be one of the exact `-H`
  files or an explicit `--public-header-dir`. A header-AST dump only ever
  parses declarations reachable by `#include` from its own `-H` root(s) in
  the first place — there is no other way for a declaration to end up in the
  snapshot at all — so a header living elsewhere under the same `-I` include
  root that the umbrella header pulled in is exactly as much a dependency of
  the public surface as the umbrella header itself, not a private
  implementation detail. This could silently drop a genuine breaking layout
  change out of the compared surface, reporting `NO_CHANGE`/exit 0 with no
  disclosure. `dumper.dump()` now folds its own `-I`/`extra_includes` roots
  into the public-directory set `provenance.apply_provenance()` classifies
  against (`include_search_dirs`), once a real `-H`/`--public-header-dir` set
  already opted classification in — a bare system-header prefix (e.g. a
  stray `-I /usr/include`) is still excluded from becoming a project
  directory, and the opt-in contract (ADR-015 D4) is unchanged: `-I` roots
  can never turn classification on by themselves.
