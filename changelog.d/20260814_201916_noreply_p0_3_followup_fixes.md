### Fixed

- **P0.3 header→compile-unit context resolution now emits `-std=` for plain
  C standards, not only C++** — `_context_flags()` previously gated
  `-std=` on `"++" in cu.standard`, silently dropping `-std=c11`/
  `-std=gnu11`/etc. for a C compile unit even though the derived context is
  documented to apply generally. Also closes review gaps left open by PR
  #762 merging before this follow-up landed: a stale docstring on
  `derive_l2_compile_context` claiming the *caller* must drain cleanups on
  the `HeaderCompileContextAmbiguousError` path (the function drains them
  itself before re-raising), a duplicated pack-resolution block between
  `derive_l2_include_dirs`/`derive_l2_compile_context` now extracted into a
  shared `_resolve_l2_seed_pack_args()` helper, missing
  `@pytest.mark.integration` markers on the real-compiler P0.3 end-to-end
  tests (the default fast test lane now correctly excludes them), and
  redundant per-header file reads/regex compiles in header→compile-unit
  `#include` matching — `_cu_references_any_header()` replaces the previous
  `_compile_unit_references_header()`, reading each compile unit's source
  once and reusing a `functools.cache`d compiled pattern per header name
  instead of re-reading and re-compiling once per (unit, header) pair.
