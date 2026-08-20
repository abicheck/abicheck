### Changed

- **The ADR-039 build-context collector is now reachable from the typed
  input-resolution pipeline, not just the ELF `dump` CLI** (CLI cleanup
  phase two, "PR C" continued). `service_input_resolution.
  _resolve_side_snapshot_impl` — shared by `compare`'s implicit-dump operand
  and `dump`'s typed `run_dump_request` API — now calls the same collector
  `perform_elf_dump` has always called directly, gated identically (a
  resolvable compile database, real parsed headers). `attach_build_context`/
  `user_define_flags`/`compile_db_from_build_info`/
  `compile_db_filter_scope_error` moved from `cli_dump_helpers.py` (a
  CLI-presentation module, not importable from a service module) to
  `header_conditionals.py`, the dependency-free leaf module that already
  owns the collector's own logic; `cli_dump_helpers.py` keeps the original
  private names resolvable — `attach_build_context`/`user_define_flags` via
  a normal import (still called directly by `perform_elf_dump`, so this
  static edge is structurally required regardless of re-export strategy),
  and `compile_db_from_build_info`/`compile_db_filter_scope_error` (never
  called internally, only re-exported for `cli.py`/tests) via a lazy
  module-level `__getattr__` shim, per this repo's own moved-helper
  convention (mirroring `cli_buildsource.py`'s identical shim) — so
  `perform_elf_dump` and every existing caller/test are unchanged.
  `perform_elf_dump` itself is not migrated to call through the shared path
  in this change — only the pipeline's own capability gap is closed, so
  `compare`'s implicit dump and `dump`'s typed API can now attach
  build-context evidence too. The collector now also expands a directory
  `InputSpec.headers` entry the same way `service.resolve_input` does
  before scanning it for `#ifdef`-guarded fields, matching
  `perform_elf_dump`'s own behavior — without this, the common
  directory-`-H` input case left `conditional_fields` silently empty even
  though the headers under it were genuinely parsed. The collector is now
  also restricted to a real ELF snapshot (`snap.elf is not None`, checked
  post-resolution rather than the pre-resolution `fmt` value — a GNU ld
  linker script's `fmt` reads as `None` before `resolve_input` follows it
  to its real ELF target), matching the established ELF-only scope of the
  ADR-039 collector (a PE/Mach-O typed dump/compare no longer silently
  disagrees with the native PE/Mach-O dump path, which never calls this
  collector). `InputSpec` deliberately does **not** gain a
  `compile_db_filter` field mirroring `--compile-db-filter` (an earlier
  version of this change added one, and Codex review found it had no
  successful execution path where it narrowed anything: this shared
  pipeline's own L2 header-AST context always resolves from the whole,
  unfiltered compile database regardless of collect mode, so the field
  could only ever be combined with a resolvable database by raising, never
  by actually narrowing the collector's scan) — a real implementation needs
  the filter threaded into the shared L2 fold itself, left as a documented
  gap for a future, separate change.

