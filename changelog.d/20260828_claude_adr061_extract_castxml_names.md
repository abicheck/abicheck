### Changed

- **ADR-061 Phase 5 item 1, first slice: opened the `abicheck.extract`
  responsibility package and moved its first tenant out of
  `dumper_castxml.py`.** Item 1 ("split CastXML and Clang parsing by entity
  and shared parser context") is the largest remaining piece of Phase 5 — a
  ~1900-line stateful `_CastxmlParser` whose methods all read shared state
  off `self` cannot move in one pass without becoming exactly the kind of
  unverified mechanical rewrite this repository's own bug-class analysis
  warns against, so this PR starts with the one piece that genuinely has no
  shared-context coupling: the module-level vtable-index/mangled-name/
  synthetic-key helpers that sat above `_CastxmlParser` (`_parse_vtable_
  index`, `_vt_sort_key`, `_ref_qualifier_from_mangled`, `_mangled_name_is_
  local_linkage`, `is_synthetic_ctor_key`/`is_synthetic_dtor_key` plus their
  `SYNTHETIC_CTOR_KEY_PREFIX`/`_SYNTHETIC_DTOR_KEY_PREFIX` constants, and
  `_virtual_method_mangled_name`) — each a pure computation over a string or
  a single XML element, none of them touching the parser's id map or any
  other instance state.

  These moved to `abicheck/extract/headers/castxml/names.py` (new
  `abicheck.extract` package, matching ADR-061 D2's target layout:
  `extract/headers/{castxml,clang}/`). `dumper_castxml.py` imports and
  re-exports every one of them under their original names, so every
  existing caller — `dumper.py`'s own re-export block, `diff_symbols.py`,
  `diff_symbols_renames.py`, `diff_templates.py`, `finding_identity_ctor_
  dtor.py`, `tu_merge.py`, `dumper_hybrid.py`, `dumper_manifest.py`,
  `buildsource/ctor_export_match.py`, and every test importing from
  `abicheck.dumper`/`abicheck.dumper_castxml` directly — keeps resolving
  unchanged. `dumper_castxml.py` drops from 1934 to 1822 lines; its
  `architecture/debt.yaml` no-growth entry is untouched (still well under
  its recorded baseline) since the rest of the file — the stateful parser
  entity-by-entity split this item still owes — has not moved yet.

  New: `abicheck/extract/AGENTS.md` (ADR-061 D1/D11 scoped package
  instructions, required once any migrated source physically lives under
  `abicheck/extract/` — `scripts/check_architecture.py`'s
  `scoped-instructions` check enforces this). It documents the package's
  purpose, permitted imports, routing table, and records which of Phase 5's
  target subdirectories (`binary/`, `debug/`, `build/`, `source/`) are not
  created yet, per ADR-061 D2's "a directory is created only when at least
  one implementation and its tests move into it."

  `python scripts/check_architecture.py` stays at 0 errors (the new
  `extract/headers/castxml/names.py` imports only `re`/`typing`, trivially
  satisfying `extract`'s `may_import: [model, storage]`). No renderer,
  detector, or classification changed — this is a pure code-motion slice;
  `tests/test_dumper_unit.py`, `tests/test_dumper_unit_phase0.py`, and every
  other test file importing these names pass unchanged.
