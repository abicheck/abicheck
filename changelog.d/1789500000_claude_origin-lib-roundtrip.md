### Fixed

- A stored snapshot (including every snapshot-cache hit) now reads back
  `ElfSymbol.origin_lib`. It was written but dropped on decode, so a warm run
  lost every `symbol_leaked_from_dependency_*` finding a cold run reported.
  The snapshot cache key version is bumped, so entries computed by the old
  heuristic are not served.
- A std template instantiated over the library's own type (e.g.
  `std::_Sp_counted_deleter<dnnl_stream*, dnnl_status_t(*)(dnnl_stream*), ...>`)
  is no longer attributed to libstdc++. The runtime cannot export a symbol
  over a type it never saw, so these are native, not leaked dependency
  symbols (`extract/mangled_foreign_template_args.py`, fail-closed on any
  mangling it does not model).
- The header graph's clang pass is no longer all-or-nothing: when the batch
  parse fails, the headers are re-parsed in halves, and only those that fail
  on their own lose their call/reference edges. The pass is still recorded
  as degraded, and the message names the headers that failed.
- `compare --dry-run` now says that "effective depth" covers L3-L5
  source/build evidence only. When headers are given, it also notes that L2
  header parsing runs regardless, so `off` next to a header-TU estimate no
  longer reads as a contradiction.
