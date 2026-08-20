### Added

- **CLI cleanup phase two, PR 3A (dump/scan resolver convergence, first
  slice)**: `service_dump_pipeline.DumpResult` now carries
  `effective_includes`/`effective_compile_context` — the P0.3 L3→L2
  compile-context fold's own resolved values, computed internally by
  `execute_dump_request()` but previously discarded after use. No observable
  behavior change to `run_dump_request`/`execute_dump_request` themselves;
  this is purely additive, laying the groundwork for `perform_elf_dump` and
  `scan_engine`'s candidate resolution to eventually route through the same
  shared primitive (`service_input_resolution._resolve_side_snapshot_impl`)
  instead of each independently re-deriving these values, closing the risk
  that the hand-rolled copies drift from the typed pipeline over time. See
  `docs/contribute/plans/cli-cleanup-phase-two.md`'s PR 3A section for what
  remains open.
