### Changed

- **ADR-061 Phase 5: castxml function-entity parsing split out of
  `dumper_castxml.py`.** `parse_functions()` and its full private call
  graph now live in `abicheck/extract/headers/castxml/functions.py` as
  free functions taking the shared `CastxmlParserContext` explicitly, the
  second entity module built on that context after `enums.py`.
  `qualified_name`/`decl_is_public`/`visibility`/`access_level` — each
  read by more than one entity kind's parsing, not just functions — moved
  into `extract/headers/castxml/location.py` instead. `_CastxmlParser`'s
  matching methods are now one-line delegations; every existing caller
  (including tests reading a parser's private methods directly) keeps
  working unchanged, and there is no output/snapshot behavior change. The
  clang backend's own `parse_functions`, and `records.py`/`templates.py`
  on both backends, remain open for the next slice — see ADR-061's Phase 5
  section for why the clang side didn't move in this pass.

