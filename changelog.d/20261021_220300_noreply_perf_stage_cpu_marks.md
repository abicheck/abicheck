### Changed

- **The memory trace now attributes CPU per stage.** Every
  `ABICHECK_MEMORY_TRACE` sample records `thread_cpu_s` and `process_cpu_s`,
  and new stage marks bound binary/debug extraction (`dump.*`), each
  `compare()` stage (`compare.prologue/detectors/dispose/assemble/ledger/rollup`),
  per-member release bookkeeping (`release.member.*`) and report rendering
  (`report.render`, `report.release_render`). Off by default and one boolean
  test per site when off; see `docs/contribute/memory.md`.
