<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`dump --depth` is now a strict contract when passed explicitly** — an
  explicit `--depth headers`/`build`/`source` that does not actually reach
  that evidence layer now fails the command instead of silently writing a
  weaker snapshot with only a warning. `--depth source --ast-frontend hybrid`
  is now rejected outright (L4 source-ABI replay has no dual-backend hybrid
  extractor, unlike the L2 header AST) rather than silently degrading while
  still calling itself "hybrid". The bare default (no `--depth`) is
  unaffected and continues to degrade honestly, as reported by
  `evidence_depth_label`.
- **Public C++ variable removals are no longer missed by broad namespace
  suppression** — `MarkReachability`'s public-header reachability check now
  resolves `Change.qualified_name` for `VAR_ADDED`/`VAR_REMOVED` findings the
  same way it already did for functions, so a mangled `Change.symbol` (e.g.
  `_ZN2ns6detail3varE`) correctly matches the demangled `Variable.name` (e.g.
  `ns::detail::var`) recorded by the public-header reachability seed. Fixes
  a gap where a broad `namespace: "**::detail::*"` suppression rule could
  silently hide a public C++ variable removal that a function removal in the
  same namespace would already have survived.
