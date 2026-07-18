<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G28 Phase 4 layout tool**: `abicheck-clang-layout-tool` only defined a
  `VisitCXXRecordDecl` callback, so a plain C header (`--lang c`) produced no
  layout records at all — C has no `CXXRecordDecl` nodes. Added a
  `VisitRecordDecl` path (guarded against double-emission for genuine C++
  classes, which `RecursiveASTVisitor` visits via both callbacks) that emits
  size/alignment/data-size/field-offset facts for a C `struct`/`union`,
  omitting the C++-only traits/bases/vptr keys.
- The `abicheck dump` CLI path for ELF binaries (`cli_dump_helpers.
  perform_elf_dump`) calls `dumper.dump()` directly and never reached the
  G28 Phase 4 layout enrichment (`attach_clang_layout`), even though
  `compare`'s implicit-dump path (`service.run_dump`) already applied it —
  the same "this path bypasses `service.py`" gap already fixed for
  `header_graph`/G14/G23/G26. A saved JSON baseline written via
  `abicheck dump --ast-frontend clang` never carried the tool's real
  size/offset/base/vptr facts. Now wired in alongside the header-graph
  attach, reusing the same `effective_compile_context`.
- `_diff_param_defaults`'s producer-mismatch skip (added for G28 Phase 3
  hybrid snapshots) only checked `ast_producer == "hybrid"` on either side,
  so a comparison between two *pure* single-backend snapshots — e.g. a
  `--ast-frontend clang` baseline against a `--ast-frontend castxml` one,
  no hybrid merge involved at all — left the mismatch unguarded. Since
  `dumper_clang.py` records default values as literals/structural
  fingerprints while castxml keeps the source expression text, an
  unchanged non-literal default could be reported as
  `PARAM_DEFAULT_VALUE_CHANGED`/`_REMOVED` purely from the representation
  difference. `fact_producer()` already resolves the producer
  unconditionally for non-hybrid snapshots too, so the same
  known-and-different skip now applies regardless of whether either side
  is hybrid.
