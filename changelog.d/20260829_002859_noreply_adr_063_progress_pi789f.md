<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **`execute_dump_request` can now carry the legacy `-p`/`--compile-db`
  auto-match's derived flags** — a new, additive
  `legacy_compile_db_tokens` parameter (default `()`, so every existing
  caller is unaffected) lets a typed-pipeline caller thread the same
  compile-database-derived castxml flags the `dump` CLI's legacy
  `-p`/`--compile-db` auto-match already derives, with the P0.3 L3->L2
  fold's own result still winning outright whenever it independently
  matches a header. ADR-063 Phase 1 progress; the real `dump` CLI's
  ELF/PE/Mach-O run does not pass this parameter yet (it still executes
  through `perform_elf_dump`/`handle_non_elf_dump`, not
  `execute_dump_request` — see `docs/contribute/known-gaps.md`'s
  "ADR-063 Phase 1" entry for what remains open).
