### Changed

- Internal (ADR-061): moved four root dump modules to their layer owners and
  deleted the old paths, with no re-export: `dumper_elf_fallback` ->
  `abicheck.workflows.dump.elf_fallback`, `service_dump_pipeline` ->
  `abicheck.workflows.dump.pipeline`, `service_header_scoped` ->
  `abicheck.workflows.dump.header_scoped`, and `dumper_cache` ->
  `abicheck.storage.header_ast_cache`. `abicheck.deadline` (a stdlib-only
  leaf) is listed as a public root surface. No behaviour change.
