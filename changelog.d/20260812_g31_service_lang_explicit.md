### Fixed

- **`compare`'s implicit-dump path, the typed `DumpRequest`/`CompareRequest`
  Python API, and PE/Mach-O now also honor an explicit `--lang c++`/`lang`
  request on a language-ambiguous header** — extending the `dump` CLI fix
  above one layer down. `service.resolve_input`/`run_dump` and their
  PE/Mach-O/ELF helpers gained an additive `lang_explicit` parameter
  (default `False`, a no-op for every existing caller) so the primary
  snapshot pass and the header-only graph pass agree on the same
  explicit-vs-auto-detected decision; `DumpRequest`/`CompareRequest` carry
  the same flag, and `compare`'s CLI resolves it the same way `dump` already
  does (via Click's own parameter-source tracking). The whole-snapshot disk
  cache key now folds this in too, since the identical `lang` string can
  resolve to two different parsed ASTs depending on it.
