### Changed

- **CodeFactor cleanup, no behavior change** — `qualified_name_scope_components`
  (`diff_cxx_rules.py`) and the build-query dry-run preview
  (`cli_dump_dry_run_build_query.py`) are split into small phase helpers, and
  the three copies of the type-name token scan (`_type_identifiers`/
  `_TYPE_NOISE`) in `surface.py`, `compare/surface_graph.py` and
  `policy/public_surface.py` are now one owner, `model/type_identifiers.py`.
