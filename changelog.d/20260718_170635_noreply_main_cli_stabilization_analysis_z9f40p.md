<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`scan --against` no longer advertises unsupported directory/package inputs** —
  the help text and docs claimed `--against` accepted "a previous dump, library,
  directory, or package", but the implementation only ever resolved a single
  file; the option now rejects directories at the CLI level (`dir_okay=False`)
  and its docs point at `abicheck compare OLD_PACKAGE NEW_PACKAGE` for
  directory/package comparisons.
- **`compare --used-by`/`--required-symbol(s)` JSON `summary` could contradict
  `changes`** — scoped-only findings (e.g. `consumer_required_symbol_removed`,
  `pe_ordinal_retargeted`) and missing-contract labels were folded into the
  JSON `changes` array after `summary` was already computed, so a scoped run
  gated only by one of these synthetic entries could report e.g.
  `"verdict": "BREAKING"` next to `"summary": {"total_changes": 0}`. `summary`
  now reflects the complete `changes` array; the pre-scoped counts move to a
  new `full_summary` key.
- **`dump`'s persisted snapshot and the `actions/baseline` manifest now record
  the actual depth contract** — a `dump_provenance` block
  (`requested_depth`/`effective_depth`/`degraded`/`frontend`/`source_scope`)
  is written into every dumped `.abi.json`'s JSON and threaded into each
  library's manifest artifact entry, so a later reader can tell how deep a
  published snapshot really goes without re-deriving it.
- **`tools/clang-layout-tool`'s CMake project no longer fails on a C-only
  try-compile** — `project(...)` now declares both `C CXX` (some LLVM/Clang
  CMake config packages run a C-language try-compile even for a C++-only
  consumer).

