### Changed

- **Clang vtable reconstruction moved into the clang backend package** — `abicheck/dumper_clang_vtable.py` is now `abicheck.extract.headers.clang.vtable` (`build_vtable`, `is_record_definition`), so the clang parser modules no longer import back up into a flat root module. Its back-compat re-exports of the template-specialization helpers and its `build_specialization_index` wrapper are removed; import those from their owner, `abicheck.extract.headers.clang.templates`. Its no-growth debt entry is retired. Dump output is unchanged.
