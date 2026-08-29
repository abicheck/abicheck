### Changed

- **The castxml/clang header-AST parsers gained a real shared-context design**
  (ADR-061 Phase 5 item 1). `abicheck.extract.headers.castxml` now has
  `context.py` (the id-map/tag-grouped-element/memoization state
  `_CastxmlParser` used to carry directly on `self`), `location.py`
  (built-in-origin and source-location resolution), and `type_resolution.py`
  (the full type-graph walk: spelling, pointer depth, alignment,
  cv/restrict qualification) — each taking a `CastxmlParserContext`
  explicitly rather than being a bound method on the parser class.
  `abicheck.extract.headers.clang` gained the matching `context.py` (the
  `_Decl` categorized-node type, built-in-file/qualtype/location/deprecation
  helpers) for the clang backend. `enums.py` is the first entity module on
  each side, both now calling through their package's shared context instead
  of `self`. `dumper_castxml.py`/`dumper_clang.py` keep every existing
  method/module-level name as a thin delegating wrapper, so no public or
  private import path changed and no snapshot output changed. Records/
  functions/templates are not split out yet — see the ADR's own Phase 5
  status for what's still coupled and why.
