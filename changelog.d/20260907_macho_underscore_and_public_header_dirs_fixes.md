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
  double-strips a name that already arrives pre-normalized. **The same
  helper also normalizes a genuine `extern "C"`/plain-C bare-name
  declaration's Darwin decoration** (`_c_func` -> `c_func`), a narrower
  residual real compiled Mach-O output on macOS CI caught after the
  Itanium-only fix above: an explicit `extern "C"` block's decorated
  `mangledName` was still left unstripped in the stored `mangled` field
  even though `is_extern_c` detection was already correct, so a self-
  comparison of a library exposing a plain `extern "C"` function alongside
  C++ symbols still reported the same contradictory pair, just for the
  C-linkage declaration instead of the C++ one. Gated on the caller's own
  already-computed `is_extern_c` boolean (`entry.extern_c` or the
  existing bare-name-equality heuristic) so it only fires when that
  determination has already concluded the true identity is the bare
  name — a bare `_foo` with no such evidence is still left untouched.
- **`crosscheck.py`'s own export-table correlation (`model.export_index.
  default_versioned_names`) no longer double-strips a Mach-O export's
  leading underscore.** A separate, independent bug from the two above:
  `macho_metadata` already strips the platform's one leading underscore
  while parsing the real export trie/symtab, so `default_versioned_names`
  re-stripping it corrupted every real Itanium C++ export (`_ZN2ns3fooEv`
  -> `ZN2ns3fooEv`), which then never correlated against the now-correctly
  singly-stripped declared identity above — a self-comparison of a real
  compiled Mach-O library still reported the same contradictory
  `exported_not_public`/`public_not_exported` pair even after both
  `Function.mangled` fixes landed. This bug predates both of the above and
  was latent all along, only invisible because `Function.mangled` used to
  be doubly-stripped too, so both sides of the comparison happened to
  agree on the same wrong spelling.
- **The export-evidence C++ language-mode fallback (added in the prior
  fragment above) now correlates the mangled export with an identifier the
  specific header under parse actually declares**, instead of accepting any
  mangled export anywhere in the binary's whole `exported_dynamic |
  exported_static` union. Without this, a genuinely plain-C header (e.g.
  `struct options { int new; };`, valid C but a parse error in C++) could
  be wrongly forced into C++ mode by an unrelated C++ export from a
  different header in the same multi-header binary.
- **That same correlation's candidate-identifier collection now only looks
  at active declaration text**, not a comment, a string/char literal, or an
  unreachable `#if 0`/`#if false` block. Without this, a name that only
  appears in `// TODO: compute this differently` or `"compute"` could
  still wrongly correlate against a real, unrelated `compute(...)` export
  elsewhere in the binary — the same over-correlation bug one level less
  obviously. Reuses the existing comment/string/raw-string/inactive-block
  stripper already used for C++20 construct detection
  (`dumper_ast_config_cpp20._preprocessed_header_content`) rather than a
  second stripper.
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
