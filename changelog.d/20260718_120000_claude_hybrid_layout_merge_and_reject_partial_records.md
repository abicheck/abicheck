<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- The hybrid merge (`dumper_hybrid.merge_snapshots`) kept the castxml
  `RecordType`/`TypeField` verbatim for every type/field name castxml also
  saw, never backfilling `data_size_bits`/`is_standard_layout`/
  `is_trivially_copyable`/`vptr_offset_bits`/`base_offsets`/per-field
  `offset_bits` from an already-enriched clang sub-dump (G28 Phase 4's
  optional `ABICHECK_CLANG_LAYOUT_TOOL`). Since castxml never populates
  `data_size_bits`/`is_standard_layout`/`is_trivially_copyable` at all, a
  type present on *both* backends — the common case — silently lost every
  one of the layout tool's facts in a hybrid dump, while a clang-only type
  kept them. Now backfilled the same way `clang_layout_tool.py` itself
  backfills a plain `--ast-frontend clang` snapshot: only into a currently-
  `None`/empty field, never overriding castxml's own real value.
- `abicheck-clang-layout-tool` reports `"ok": false` for a recoverable-error
  parse alongside whatever partial records it still produced for the
  declarations it visited, but `run_layout_tool` accepted those records
  regardless of `ok`, only ever checking JSON validity. That let a hybrid/
  clang snapshot carry layout facts for an arbitrary, silently-incomplete
  subset of records instead of cleanly degrading to no enrichment — the
  same "the L2 header AST must be complete to be authoritative" contract
  the main clang dump's own parse-result validation already enforces. Now
  rejects the whole run (returns no records) unless `ok` is `true`.
