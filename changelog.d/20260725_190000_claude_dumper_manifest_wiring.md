<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`dumper.dump()` gains a `dump_manifest` parameter (ADR-050 D3, G32
  Phase B)**: a real multi-TU dump for ELF binaries, running one
  castxml/clang invocation per translation unit
  (`dumper_manifest.resolve_header_ast_result`/`run_tu_loop`) instead of a
  single flat header parse, merged via a strict placeholder (concatenate,
  error loudly on any duplicate declaration across TUs — the real
  ODR-aware merge lands in a later phase). The existing single-header path
  now routes through the same per-TU machinery as its own one-TU case, not
  a parallel implementation. `dump_manifest` is mutually exclusive with
  `headers`/`public_headers`/`public_header_dirs` and with the `hybrid`
  AST frontend; PE/Mach-O reject a manifest outright for now (ELF-only,
  matching this phase's own fixture scope). No CLI flag yet — that's a
  follow-up.
