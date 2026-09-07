### Fixed

- **A bare `--ast-frontend clang` dump on Darwin no longer disagrees with
  its own binary's export table on C++ mangled-name spelling.** Only the
  castxml+clang hybrid-merge path previously stripped Mach-O's linker-added
  leading underscore from a real Itanium mangled name (`__Z...` ->
  `_Z...`); a clang-only dump left `Function.mangled`/`Variable.mangled`
  decorated, so `crosscheck.py`'s `exported_not_public`/
  `public_not_exported` checks (and the underlying entity-identity tag)
  disagreed with the binary's own already-normalized export table for an
  unchanged declaration. Both `extract.headers.clang.functions.
  parse_functions` and `dumper_clang.parse_variables` now normalize this at
  parse time too, via a new shared `extract.headers.clang.context.
  strip_darwin_itanium_decoration` helper; `dumper_hybrid.
  _macho_normalize_mangled` was made idempotent so it no longer
  double-strips a name that already arrives pre-normalized.
- **The export-evidence C++ language-mode fallback (added in the prior
  fragment above) now correlates the mangled export with an identifier the
  specific header under parse actually declares**, instead of accepting any
  mangled export anywhere in the binary's whole `exported_dynamic |
  exported_static` union. Without this, a genuinely plain-C header (e.g.
  `struct options { int new; };`, valid C but a parse error in C++) could
  be wrongly forced into C++ mode by an unrelated C++ export from a
  different header in the same multi-header binary.
- **`dumper_ast_config._cache_key` now hashes the resolved `force_cpp`
  decision**, closing a stale-cache risk where a header previously parsed
  in C mode could keep serving that cached AST after later export evidence
  correctly resolved the same header to C++.
- **A project's `.abicheck.yml` `scope.public_header_dirs` now reaches a
  directory/package `compare` (the release fan-out) and its own
  "stranded library" fallback dump**, not just a bare two-file `compare`.
  `service_compare_pipeline.run_compare`'s keyword-argument shim gained a
  `public_header_dirs` parameter, threaded through
  `cli_compare_release_pairwise`'s per-library comparison and
  `cli_compare_release.compare_release_cmd`'s `_resolve_stranded_library`
  closure's own inline `InputSpec` construction — previously both silently
  dropped the project's declared header-boundary scoping even though the
  identical library compared alone already honored it.
