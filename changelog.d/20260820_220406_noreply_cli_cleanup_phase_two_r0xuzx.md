### Changed

- **The ADR-039 build-context collector is now reachable from the typed
  input-resolution pipeline, not just the ELF `dump` CLI** (CLI cleanup
  phase two, "PR C" continued). `service_input_resolution.
  _resolve_side_snapshot_impl` — shared by `compare`'s implicit-dump operand
  and `dump`'s typed `run_dump_request` API — now calls the same collector
  `perform_elf_dump` has always called directly, gated identically (a
  resolvable compile database, real parsed headers). `InputSpec` gained
  `compile_db_filter` (mirrors `--compile-db-filter`) so a caller of the
  typed API can narrow the collector's compile-DB scan the same way the CLI
  flag does. `attach_build_context`/`user_define_flags`/
  `compile_db_from_build_info` moved from `cli_dump_helpers.py` (a
  CLI-presentation module, not importable from a service module) to
  `header_conditionals.py`, the dependency-free leaf module that already
  owns the collector's own logic; `cli_dump_helpers.py` keeps the original
  private names as thin re-exports, so `perform_elf_dump` and every
  existing caller/test are unchanged. `perform_elf_dump` itself is not
  migrated to call through the shared path in this change — only the
  pipeline's own capability gap is closed, so `compare`'s implicit dump and
  `dump`'s typed API can now attach build-context evidence too. The
  collector now also expands a directory `InputSpec.headers` entry the same
  way `service.resolve_input` does before scanning it for `#ifdef`-guarded
  fields, matching `perform_elf_dump`'s own behavior — without this, the
  common directory-`-H` input case left `conditional_fields` silently empty
  even though the headers under it were genuinely parsed. The collector is
  now also restricted to `fmt == "elf"`, matching the established ELF-only
  scope of the ADR-039 collector (a PE/Mach-O typed dump/compare no longer
  silently disagrees with the native PE/Mach-O dump path, which never calls
  this collector). Combining `InputSpec.compile_db_filter` with a
  compile-database `build_info` at a non-`"off"` collect mode is now
  refused with a `ValidationError` — the typed-API equivalent of the native
  `dump` CLI's own `compile_db_filter_scope_error` usage error — since the
  filtered collector and the unfiltered L3 embed would otherwise disagree
  on which translation units the snapshot's evidence covers.

