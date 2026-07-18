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
- **`dump --dry-run --depth source` with `--build-info` but no `--sources`
  now blocks instead of reporting success** — a raw `--build-info` compile
  database supplies L3 build context only; L4 source-ABI replay only ever
  runs over a `--sources` tree, so the real (non-dry) dump's strict depth
  gate would hard-fail on this input while the dry run previously exited 0.
- **`dump_provenance`'s `effective_depth` now matches the strict depth
  gate's own verdict** — it previously used the plain (non-gated) evidence
  label, which disagrees with the gate on a zero-match source-only dump (L4
  replay ran but linked nothing); a `--depth source` dump the gate had just
  accepted could serialize `effective_depth: "build", degraded: true`.

