### Changed

- **ADR-061 Phase 5: clang function-entity parsing split out of
  `dumper_clang.py`.** `parse_functions()` and its function-only private
  call graph now live in `abicheck/extract/headers/clang/functions.py`,
  the second entity module built on that backend's shared context after
  `enums.py`. Its three pieces of extra instance state each resolved
  differently: `_virtual_mangled_names()` (and the `_record_index()`/
  `_specialization_record_index()`/`_base_lookup_index()` caches under it)
  moved into a new `RecordVtableIndex` class in
  `extract/headers/clang/context.py`, since record-entity parsing reads
  the same index; `_id_index`-based default-argument evaluation is now an
  explicit `default_value` parameter, the same treatment `enums.py`
  already established for its own excluded evaluator; `_target_triple`
  turned out to be a stateless pass-through needing no shared home at
  all. `access_level`/`visibility`/`qualified_name` — each read by more
  than one entity kind's parsing, not just functions — moved into
  `context.py` alongside the new class. `_ClangAstParser`'s matching
  methods and module-level helpers are now one-line delegations; every
  existing caller (including tests reading a parser's private methods
  directly, or importing a module-level name straight off `dumper_clang`)
  keeps working unchanged, and there is no output/snapshot behavior
  change. `records.py`/`templates.py` on both backends remain open for
  the next slice — see ADR-061's Phase 5 section for the full account.
