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
  each side: castxml's `parse_enums` takes the shared
  `CastxmlParserContext` explicitly, while clang's takes the
  pre-categorized decl lists and constant-expression evaluator
  `_ClangAstParser._walk` already produces, as separate explicit
  parameters — both calling through explicit state instead of `self`.
  `dumper_castxml.py`/`dumper_clang.py` keep every existing
  method/module-level name as a thin delegating wrapper, so no public or
  private import path changed and no snapshot output changed. Records/
  functions/templates are not split out yet — see the ADR's own Phase 5
  status for what's still coupled and why.
- **Two review findings on the above fixed before merge.** `enums.py` had
  reached back into a private helper (`_deprecation_marker`) of the still-flat
  `dumper_castxml_typedefs.py` sibling — the exact "don't reach into a flat
  legacy module's private helpers" case `abicheck/extract/AGENTS.md` warns
  against — so the primitive moved to
  `extract.headers.castxml.location.deprecation_marker`, with the flat
  module keeping a delegating `_deprecation_marker` alias. Separately,
  `scripts/backend_capabilities.py`'s AST-based capability scanner only read
  `dumper_castxml.py`/`dumper_clang.py`, so it stopped seeing the `EnumType`
  fields the split moved into the new entity modules; it now also scans each
  backend's `extract/headers/*` entity modules. Neither changes the
  published capability matrix's content — `gen_backend_capability_matrix.py
  --check` confirms it's unchanged.
